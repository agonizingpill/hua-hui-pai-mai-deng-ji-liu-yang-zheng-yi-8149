export const ROLES={operator:'场内验级员',supervisor:'拍卖主管',viewer:'观察席'};
export const GRADES=['A','B','C'];
export const SAMPLE_STATES={recorded:'待估级',queued:'排队',running:'计算中',ready:'待主管核级',approved:'已核级',failed:'估级失败'};
export const LOT_STATES={offered:'挂场',sold:'已成交',cancelled:'已撤下',returned:'复核退回'};
export const DEAL_STATES={active:'履约中',short_pending:'待买方选择',delivered_short:'按实交结算',cancelled:'整单取消'};
export const DISPOSITIONS={
  offered:'挂场待成交',
  sold:'成交足额保留',
  returned:'实收复核退回',
  short_pending:'短交待选择',
  short_delivered:'短交按实结算',
  short_cancelled:'短交整单取消',
  cancelled:'已撤场'
};
export const CHOICES={deliver:'按实交筐数结算',cancel:'整单取消并全数退款'};
