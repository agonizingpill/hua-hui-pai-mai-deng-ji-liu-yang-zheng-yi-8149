from sqlalchemy import select,func
from fastapi import HTTPException
from .models import Consignment,AuctionLot,Deal,VaseSample,Dispute,Settlement
from .common import lock,pack,add,audit
from .constants import WEIGHT,RANK,COMMISSION_PCT

def lock_deal(s,id):
    pre=s.get(Deal,id)
    if not pre:raise HTTPException(404,'成交单不存在')
    lot=s.get(AuctionLot,pre.lot_id);lock(s,Consignment,lot.consignment_id);return lock(s,Deal,id),lot

def open_dispute(s,d,u):
    deal,lot=lock_deal(s,d.deal_id);sample=s.get(VaseSample,lot.sample_id)
    if deal.state=='cancelled':raise HTTPException(409,'成交已整单取消，不能开留样争议')
    if deal.state=='short_pending':raise HTTPException(409,'实收短交待买方选择，暂不受理新争议')
    if sample.observed_days is None:raise HTTPException(409,'先完成原留样瓶插天数观察，不能凭空复判')
    if s.scalar(select(Dispute.id).where(Dispute.deal_id==deal.id,Dispute.state=='opened')):raise HTTPException(409,'已有未复判争议')
    audit(s,u,'提交留样争议');return add(s,Dispute(**d.model_dump(),sample_snapshot=pack(sample)))

def decide(s,id,d,u):
    pre=s.get(Dispute,id)
    if not pre:raise HTTPException(404,'争议不存在')
    deal,lot=lock_deal(s,pre.deal_id);claim=lock(s,Dispute,id)
    if claim.state!='opened':raise HTTPException(409,'争议已经复判，不可覆盖')
    if RANK[d.grade]<RANK[deal.effective_grade]:raise HTTPException(409,'买方质量争议不可上调等级')
    days=claim.sample_snapshot['observed_days'];observed='A'if days>=10 else'B'if days>=7 else'C'
    if RANK[d.grade]<RANK[observed]:raise HTTPException(409,'复判不可高于冻结留样天数对应等级')
    if deal.state=='cancelled':raise HTTPException(409,'成交已整单取消，不能复判')
    claim.state='decided';claim.revised_grade=d.grade;claim.decision_note=d.note;deal.effective_grade=d.grade;deal.revision+=1
    for row in s.scalars(select(Settlement).where(Settlement.deal_id==deal.id,Settlement.state=='draft')):row.state='void'
    audit(s,u,'主管冻结留样复判与结算修订 '+d.grade);return pack(claim)

def create_draft(s,deal,u,kind=None):
    crates=0 if deal.state=='cancelled' else(deal.delivered_crates if deal.delivered_crates is not None else deal.crates)
    gross=(deal.unit_cents*crates*WEIGHT[deal.effective_grade])//WEIGHT[deal.original_grade];commission=(gross*COMMISSION_PCT)//100;target=gross-commission
    paid=s.scalar(select(func.coalesce(func.sum(Settlement.delta_cents),0)).where(Settlement.deal_id==deal.id,Settlement.state=='paid'))
    kind=kind or('initial'if not paid else'adjustment')
    row=Settlement(deal_id=deal.id,revision=deal.revision,kind=kind,crates=crates,gross_cents=gross,commission_cents=commission,target_cents=target,delta_cents=target-paid,receipt_check_id=deal.receipt_check_id,snapshot={'deal':pack(deal),'crates':crates,'grade_weights':WEIGHT,'commission_pct':COMMISSION_PCT,'previous_paid_cents':paid,'shortfall':{'crates':deal.shortfall_crates,'choice':deal.shortfall_choice}})
    audit(s,u,'生成货款及补退差额');s.add(row);s.flush();return row

def draft(s,id,u):
    deal,lot=lock_deal(s,id)
    if deal.state=='short_pending':raise HTTPException(409,'实收短交待买方选择按实交结算或整单取消')
    existing=s.scalar(select(Settlement).where(Settlement.deal_id==id,Settlement.revision==deal.revision))
    if existing:return pack(existing)
    if s.scalar(select(Dispute.id).where(Dispute.deal_id==id,Dispute.state=='opened')):raise HTTPException(409,'留样争议未复判，暂不结算')
    return pack(create_draft(s,deal,u))

def pay(s,id,d,u):
    pre=s.get(Settlement,id)
    if not pre:raise HTTPException(404,'结算单不存在')
    deal,lot=lock_deal(s,pre.deal_id);row=lock(s,Settlement,id)
    if row.state=='paid':
        if row.receipt==d.receipt:return pack(row)
        raise HTTPException(409,'结算已登记回单，不能重复支付')
    if row.state!='draft' or row.revision!=deal.revision:raise HTTPException(409,'结算已支付、已作废或版本过期')
    if deal.state=='short_pending':raise HTTPException(409,'实收短交待买方选择，暂不支付')
    if s.scalar(select(Dispute.id).where(Dispute.deal_id==deal.id,Dispute.state=='opened')):raise HTTPException(409,'争议未决不可支付')
    row.state='paid';row.receipt=d.receipt;audit(s,u,'登记实际货款/补退回单 '+d.receipt);return pack(row)
