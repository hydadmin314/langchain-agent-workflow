window.SALES_DEMO_CASES = [
  {
    id: "store",
    title: "餐饮门店稳定宽带",
    type: "产品推荐",
    query: "餐饮门店5个人用，主要收银、外卖平台、监控和日常上网，预算有限，想要稳定宽带",
    expected: {
      category: "5_门店_商铺_小微经营",
      primaryProduct: "智云随选商企云宽基础版",
      alternatives: ["小微精品业务套餐", "智能专线"],
      requiredRisks: ["门店地址资源覆盖", "价格有效期", "监控上传带宽"],
      forbidden: ["按普通办公室追问内部系统或海外 SaaS", "编造未收录的套餐价格"],
    },
    rounds: [
      {
        topic: "门店范围",
        questions: ["这是单门店还是多门店，门店大致地址或所在区域在哪里？"],
        sampleReply: "单门店，在上海杨浦区。",
      },
      {
        topic: "营业连续性",
        questions: ["收银和外卖平台是否必须持续在线，断网会不会直接影响营业？"],
        sampleReply: "收银和外卖必须持续在线，断网会直接影响营业。",
      },
      {
        topic: "监控与无线",
        questions: ["监控是本地存储还是云端上传？是否需要店内 Wi-Fi 覆盖？"],
        sampleReply: "8路监控需要云端上传，也需要员工和顾客 Wi-Fi 分开。",
      },
      {
        topic: "预算与计费",
        questions: ["预算上限是多少，偏向年付、月付还是先试用？"],
        sampleReply: "预算每年7000元以内，偏向年付。",
      },
      {
        topic: "开通与兜底",
        questions: ["希望什么时候开通，是否需要4G/5G备线或临时过渡方案？"],
        sampleReply: "希望下月开通，需要一条移动网络作为断网兜底。",
      },
    ],
  },
  {
    id: "voice",
    title: "30坐席呼叫中心",
    type: "产品推荐",
    query: "客户有呼叫中心，30个坐席电话，需要总机、中继线和来电转接",
    expected: {
      category: "6_固定电话_语音中继_呼叫业务",
      primaryProduct: "中国电信号百集团云中继",
      alternatives: ["商云通", "语音融合专线"],
      requiredRisks: ["号码资源", "外呼合规", "实名及授权材料"],
      forbidden: ["把普通互联网专线作为主推", "忽略坐席并发"],
    },
    rounds: [
      {
        topic: "建设方式",
        questions: ["这是新建呼叫中心，还是已有总机、PBX或号码需要迁移？"],
        sampleReply: "新建呼叫中心，没有旧总机，但希望使用统一总机号码。",
      },
      {
        topic: "坐席并发",
        questions: ["30个坐席中，最忙时预计有多少人会同时通话？是否所有坐席都需要拨打外部电话？"],
        sampleReply: "预计最高15路并发，全部坐席都需要外呼。",
      },
      {
        topic: "号码能力",
        questions: [
          {
            text: "是否需要分机号和来电转接？外部客户是否需要绕过总机，直接拨到具体坐席？",
            helper: "如果需要直接拨到具体坐席，通常会为坐席配置可直接拨入的号码。",
          },
        ],
        sampleReply: "需要分机号、IVR导航、录音和来电转接，不需要DID直线号码。",
      },
      {
        topic: "合规控制",
        questions: ["是否需要黑白名单、外呼频控？所属行业是否有额外监管要求？"],
        sampleReply: "需要黑名单和外呼频控，属于普通企业客服。",
      },
      {
        topic: "预算开通",
        questions: ["预算范围、计费偏好和期望开通时间是什么？"],
        sampleReply: "预算每年2万元以内，希望年付，一个月内开通。",
      },
    ],
  },
  {
    id: "overseas",
    title: "海外 SaaS 访问优化",
    type: "产品推荐",
    query: "上海办公室10人访问美国 SaaS 很慢，预算5000左右，希望提升访问速度",
    expected: {
      category: "4_海外访问_跨境业务",
      primaryProduct: "海外访问优化或跨境专线候选",
      alternatives: ["智能专线", "精品专线"],
      requiredRisks: ["跨境合规", "目标应用和地区", "资源开通可行性"],
      forbidden: ["承诺跨境访问效果", "忽略合规人工确认"],
    },
    rounds: [
      {
        topic: "目标应用",
        questions: ["美国 SaaS 具体是什么应用，主要访问哪个国家或地区？"],
        sampleReply: "主要访问美国的CRM和在线设计平台。",
      },
      {
        topic: "访问规模",
        questions: ["访问来源有几个地点，各有多少人或终端？"],
        sampleReply: "只有上海办公室一个地点，10个人使用。",
      },
      {
        topic: "故障表现",
        questions: ["当前慢的表现是什么，例如登录慢、下载慢或视频会议卡顿？"],
        sampleReply: "登录和打开页面慢，上传设计文件也很慢。",
      },
      {
        topic: "网络要求",
        questions: [
          {
            text: "官网或服务器是否需要长期通过同一个固定的网络地址供外部访问？另外，是否希望先试用验证效果？",
            helper: "这种长期不变化、可供外部访问的互联网地址通常叫固定公网 IP。",
          },
        ],
        sampleReply: "不需要固定IP，希望先试用一个月验证效果。",
      },
      {
        topic: "预算开通",
        questions: ["预算是月预算还是年预算，期望什么时候开通？"],
        sampleReply: "5000元是月预算，希望两周内可以试用。",
      },
    ],
  },
  {
    id: "networking",
    title: "总部与分公司组网",
    type: "产品对比",
    query: "上海总部访问新疆子公司业务慢，需要提高访问速率并保障稳定",
    expected: {
      category: "3_专线_组网_多点互联",
      primaryProduct: "智能专线",
      alternatives: ["精品专线", "SD-WAN组网候选"],
      requiredRisks: ["两端资源覆盖", "带宽和时延目标", "备份线路"],
      forbidden: ["只按办公人数推荐普通宽带", "忽略两端站点"],
    },
    rounds: [
      {
        topic: "站点信息",
        questions: ["需要互联的站点有几个，分别在哪些城市或地址？"],
        sampleReply: "目前两个站点，上海总部和新疆乌鲁木齐子公司。",
      },
      {
        topic: "组网模式",
        questions: ["是点对点互联，还是后续还会增加其他分支？"],
        sampleReply: "当前点对点，明年可能再增加两个分公司。",
      },
      {
        topic: "业务规模",
        questions: ["每个站点大约多少人使用，核心访问的业务系统是什么？"],
        sampleReply: "上海60人、新疆20人，主要访问ERP和文件系统。",
      },
      {
        topic: "性能保障",
        questions: ["期望带宽、时延、备份线路或安全隔离要求是什么？"],
        sampleReply: "希望至少50M稳定带宽，需要备份线路和业务隔离。",
      },
      {
        topic: "预算周期",
        questions: ["预算范围、开通周期和月付/年付偏好是什么？"],
        sampleReply: "预算每年10万元以内，希望两个月内开通，偏向年付。",
      },
    ],
  },
  {
    id: "fixed_ip",
    title: "企业服务器固定公网IP",
    type: "产品对比",
    query: "企业官网和服务器需要对外访问，20人办公，需要固定公网IP和稳定带宽",
    expected: {
      category: "2_固定IP_高带宽_互联网专线",
      primaryProduct: "具备固定公网IP证据的互联网专线",
      alternatives: ["IPMAN候选", "精品专线"],
      requiredRisks: ["IP数量", "备案和安全防护", "上行带宽"],
      forbidden: ["推荐不含公网IP证据的普通宽带", "把5G误认为带宽"],
    },
    rounds: [
      {
        topic: "服务器用途",
        questions: ["服务器承载官网、接口还是其他对外业务？"],
        sampleReply: "承载企业官网和客户查询接口。",
      },
      {
        topic: "公网IP",
        questions: [
          {
            text: "官网和客户查询接口是否需要长期通过同一个网络地址供外部访问？如果需要，大概有几个独立系统？",
            helper: "这种长期不变化的互联网地址通常叫固定公网 IP；不同系统是否需要独立地址，要结合实际部署确认。",
          },
        ],
        sampleReply: "需要2个固定公网IP，域名已有，备案需要协助。",
      },
      {
        topic: "带宽方向",
        questions: ["期望上下行带宽是多少，外部访问峰值大约多高？"],
        sampleReply: "希望上下行至少50M，访问量不大但要稳定。",
      },
      {
        topic: "安全要求",
        questions: ["是否需要防火墙、DDoS防护、端口开放或访问控制？"],
        sampleReply: "需要基础防火墙和DDoS防护，需要开放HTTPS端口。",
      },
      {
        topic: "预算开通",
        questions: ["预算范围、协议期和期望开通时间是什么？"],
        sampleReply: "预算每月5000元以内，接受一年协议，下月开通。",
      },
    ],
  },
  {
    id: "pricing",
    title: "指定套餐资费查询",
    type: "资费查询",
    query: "想了解100M企业宽带或商务专线年付多少钱，套餐包含什么",
    expected: {
      category: "1_企业上网与办公宽带",
      primaryProduct: "有明确100M及年付价格证据的候选",
      alternatives: ["同档位其他运营商或产品"],
      requiredRisks: ["价格有效期", "地区资源", "协议期和安装费"],
      forbidden: ["没有资料证据时直接报价", "混用月付和年付价格"],
    },
    rounds: [
      {
        topic: "办理地区",
        questions: ["客户办理地址或所在城市在哪里？"],
        sampleReply: "办理地址在上海浦东新区。",
      },
      {
        topic: "带宽口径",
        questions: ["100M是期望上行、下行，还是上下行对称？"],
        sampleReply: "希望下行至少100M，上行越高越好，不强制对称。",
      },
      {
        topic: "使用场景",
        questions: ["主要用于办公上网、视频会议、上传文件还是服务器对外？"],
        sampleReply: "主要办公上网和视频会议，没有服务器对外。",
      },
      {
        topic: "套餐能力",
        questions: [
          {
            text: "是否有官网或服务器需要长期使用同一个网络地址供外部访问？另外是否需要企业固话或无线覆盖？",
            helper: "长期不变化、可供外部访问的互联网地址通常叫固定公网 IP；普通办公上网一般不一定需要。",
          },
        ],
        sampleReply: "不需要固定IP，希望能带一条企业固话。",
      },
      {
        topic: "计费确认",
        questions: ["只看年付价格，还是也需要对比月付、协议期和一次性费用？"],
        sampleReply: "重点看年付，也要说明协议期和有没有安装调测费。",
      },
    ],
  },
  {
    id: "service_process",
    title: "宽带移机办理材料",
    type: "办理流程",
    query: "公司办公室要搬迁，现有宽带需要移机，想知道办理流程和需要哪些材料",
    expected: {
      category: "13_办理变更_续约_拆机_撤单",
      primaryProduct: "办理流程与材料说明",
      alternatives: [],
      requiredRisks: ["新地址资源", "停机窗口", "合同主体和授权材料"],
      forbidden: ["按新销售套餐强行推荐", "遗漏原业务号码或合同信息"],
    },
    rounds: [
      {
        topic: "办理类型",
        questions: ["确认是同城移机、跨区移机，还是原址拆机后新装？"],
        sampleReply: "上海市内跨区搬迁，希望办理移机。",
      },
      {
        topic: "原业务信息",
        questions: ["原业务号码、合同编号和客户名称是否已经明确？"],
        sampleReply: "业务号码和合同编号都有，合同主体不变。",
      },
      {
        topic: "新地址资源",
        questions: ["新办公地址在哪里，是否已经确认资源覆盖？"],
        sampleReply: "新地址在上海徐汇区，还没有确认资源。",
      },
      {
        topic: "办理材料",
        questions: ["营业执照、经办人证件、授权书和盖章材料是否已经准备？"],
        sampleReply: "营业执照和经办人证件已准备，授权书还没有。",
      },
      {
        topic: "业务连续性",
        questions: ["期望完成时间是什么，是否要求搬迁期间业务不中断？"],
        sampleReply: "一个月内完成，最好新线路开通后再拆旧线路。",
      },
    ],
  },
  {
    id: "clarify",
    title: "模糊网络方案咨询",
    type: "需求澄清",
    query: "客户想了解一下网络方案",
    expected: {
      category: "clarify_required",
      primaryProduct: "信息不足时不推荐具体产品",
      alternatives: [],
      requiredRisks: ["先确认业务目标", "来源和目标", "规模预算周期"],
      forbidden: ["第一轮直接推荐产品", "一次追问过多问题"],
    },
    rounds: [
      {
        topic: "业务目标",
        questions: ["客户主要想解决上网、组网、海外访问、语音电话还是云和IDC问题？"],
        sampleReply: "主要是上海办公室访问新疆子公司的内部系统。",
      },
      {
        topic: "来源目标",
        questions: ["请确认访问来源、目标地点和需要互联的站点数量。"],
        sampleReply: "上海办公室访问乌鲁木齐子公司，目前两个站点。",
      },
      {
        topic: "人员系统",
        questions: ["大约多少人使用，主要访问什么业务系统？"],
        sampleReply: "上海30人、新疆10人，主要访问ERP和文件系统。",
      },
      {
        topic: "性能要求",
        questions: ["当前问题是什么，是否有带宽、时延、稳定性或备线要求？"],
        sampleReply: "访问速度慢，要求稳定，最好有备份线路。",
      },
      {
        topic: "预算开通",
        questions: ["预算范围、计费偏好和期望开通时间是什么？"],
        sampleReply: "预算每年8万元，偏向年付，两个月内开通。",
      },
    ],
  },
];
