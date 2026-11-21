from sqlalchemy import select,func
from fastapi import HTTPException
from .models import Clock,Consignment,AuctionLot,Deal,Settlement,ReceiptCheck
from .common import lock,pack,audit

LIVE_LOT_STATES=('offered','sold')
CHOICES=('deliver','cancel')

def verified(s,consignment_id):
    return s.scalar(select(ReceiptCheck).where(ReceiptCheck.consignment_id==consignment_id))

def effective_crates(s,cons):
    check=verified(s,cons.id)
    return check.verified_crates if check else cons.crates

def allocated_total(s,consignment_id):
    return s.scalar(select(func.coalesce(func.sum(func.coalesce(AuctionLot.allocated_crates,AuctionLot.crates)),0)).where(AuctionLot.consignment_id==consignment_id,AuctionLot.state.in_(LIVE_LOT_STATES))) or 0

def close_order(s,consignment_id):
    rows=s.execute(select(AuctionLot,Deal).join(Deal,Deal.lot_id==AuctionLot.id).where(AuctionLot.consignment_id==consignment_id,AuctionLot.state=='sold',Deal.state!='cancelled').order_by(Deal.at.asc(),Deal.id.asc())).all()
    return rows

def _replay(s,check):
    return {'check':pack(check),'replayed':True,
            'deals':[pack(x)for x in s.scalars(select(Deal).where(Deal.receipt_check_id==check.id).order_by(Deal.id))],
            'lots':[pack(x)for x in s.scalars(select(AuctionLot).where(AuctionLot.receipt_check_id==check.id).order_by(AuctionLot.id))]}

def verify(s,d,u):
    cons=lock(s,Consignment,d.consignment_id);clock=lock(s,Clock,1)
    existing=verified(s,cons.id)
    if existing:
        if existing.verified_crates!=d.verified_crates:raise HTTPException(409,f'该到场批已按{existing.verified_crates}筐实收复核，不能改记为{d.verified_crates}筐；复核结果不可覆盖')
        return _replay(s,existing)
    occupied=allocated_total(s,cons.id)
    if d.verified_crates>cons.crates:raise HTTPException(409,'实收筐数不能超过到场登记筐数')
    shortage=max(occupied-d.verified_crates,0)
    check=ReceiptCheck(consignment_id=cons.id,verified_crates=d.verified_crates,declared_crates=cons.crates,occupied_crates=occupied,note=d.note,actor=u.email,at=clock.now,
        snapshot={'consignment':pack(cons),'occupied_crates':occupied,'verified_crates':d.verified_crates,'shortage_crates':shortage,
                  'rule':'已成交标的按截标先后保留，未成交标的退回；短交部分由买方选择按实交结算或整单取消'})
    s.add(check);s.flush()
    remaining=d.verified_crates;touched_deals=[];touched_lots=[]
    for lot,deal in close_order(s,cons.id):
        take=min(lot.crates,remaining);remaining=max(remaining-take,0);short=lot.crates-take
        lot.receipt_check_id=check.id;lot.allocated_crates=take;lot.shortfall_crates=short
        deal.receipt_check_id=check.id;deal.delivered_crates=take
        deal.snapshot=deal.snapshot or {'at_close':{'crates':deal.crates,'unit_cents':deal.unit_cents,'buyer':deal.buyer,'original_grade':deal.original_grade,'at':deal.at}}
        if short:
            lot.disposition='short_pending';lot.pending_crates=short
            lot.disposition_reason=f'截标在先保留{take}筐，实收不足短交{short}筐，待买方选择按实交结算或整单取消'
            deal.state='short_pending';deal.shortfall_crates=short
            for row in s.scalars(select(Settlement).where(Settlement.deal_id==deal.id,Settlement.state=='draft')):row.state='void'
        else:
            lot.disposition='sold';lot.pending_crates=0
            lot.disposition_reason=f'实收复核足额保留成交{take}筐（按截标先后）'
            deal.state='active'
        touched_deals.append(deal);touched_lots.append(lot)
    for lot in s.scalars(select(AuctionLot).where(AuctionLot.consignment_id==cons.id,AuctionLot.state=='offered')):
        lot.receipt_check_id=check.id
        if shortage:
            lot.state='returned';lot.disposition='returned';lot.allocated_crates=0;lot.pending_crates=0
            lot.disposition_reason='实收短于已挂与成交合计，未成交标的整批退回并释放占筐'
        else:
            lot.disposition='offered';lot.allocated_crates=lot.crates;lot.pending_crates=0
            lot.disposition_reason='实收复核足额，挂场保持，仍受实收筐数上限约束'
        touched_lots.append(lot)
    s.flush()
    audit(s,u,f'复核实收筐数 {d.verified_crates} / 登记{cons.crates}，成交按截标先后保留，未成交退回，短缺{shortage}筐')
    return {'check':pack(check),'replayed':False,'deals':[pack(x)for x in touched_deals],'lots':[pack(x)for x in touched_lots]}

def choice(s,id,d,u):
    pre=s.get(Deal,id)
    if not pre:raise HTTPException(404,'成交单不存在')
    lot_pre=s.get(AuctionLot,pre.lot_id);lock(s,Consignment,lot_pre.consignment_id);clock=lock(s,Clock,1)
    deal=lock(s,Deal,id);lot=lock(s,AuctionLot,lot_pre.id)
    if d.choice not in CHOICES:raise HTTPException(409,'只能选择按实交筐数结算或整单取消')
    if deal.shortfall_choice:
        if deal.shortfall_choice==d.choice:
            row=s.scalar(select(Settlement).where(Settlement.deal_id==deal.id,Settlement.kind=='shortfall').order_by(Settlement.revision.desc()).limit(1))
            return {'deal':pack(deal),'settlement':pack(row)if row else None,'replayed':True}
        raise HTTPException(409,'买方短交选择已登记，覆盖后无法恢复')
    if deal.state=='active' and not deal.shortfall_crates:raise HTTPException(409,'该成交无短交，无需选择')
    if deal.state not in ('short_pending','delivered_short','cancelled'):raise HTTPException(409,'该成交当前不可处置')
    for row in s.scalars(select(Settlement).where(Settlement.deal_id==deal.id,Settlement.state=='draft')):row.state='void'
    if d.choice=='deliver':
        deal.state='delivered_short';deal.shortfall_note=d.note
        lot.disposition='short_delivered';lot.pending_crates=0
        lot.disposition_reason=f'买方选择按实交{deal.delivered_crates}筐结算，差额{deal.shortfall_crates}筐按实收依据补退'
        note=f'买方选择按实交{deal.delivered_crates}筐结算'
    else:
        deal.state='cancelled';deal.delivered_crates=0;deal.shortfall_note=d.note
        lot.state='cancelled';lot.disposition='short_cancelled';lot.pending_crates=0;lot.allocated_crates=0
        lot.disposition_reason=f'买方选择整单取消，原成交{deal.crates}筐不再交付，已付货款按零交付全数退回'
        note='买方选择整单取消'
    deal.shortfall_choice='deliver'if d.choice=='deliver'else'cancel'
    deal.choice_at=clock.now;deal.choice_by=u.email;deal.revision+=1
    audit(s,u,note+f'（成交#{deal.id}）')
    from .dispute_service import create_draft
    row=create_draft(s,deal,u,kind='shortfall')
    return {'deal':pack(deal),'settlement':pack(row),'replayed':False}
