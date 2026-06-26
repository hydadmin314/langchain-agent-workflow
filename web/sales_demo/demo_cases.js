window.SALES_DEMO_CASES = [
  {
    id: "store",
    title: "沿街商铺开店宝",
    type: "上网-沿街店铺",
    minimumRounds: 0,
    query: "沿街奶茶店准备新装宽带，主要用于收银、外卖接单、店内 Wi-Fi 和监控。",
    expected: {
      category: "internet_store_street_上网-沿街店铺",
      primaryProduct: "开店宝或旺铺宽带套餐",
      alternatives: ["智联（沃商铺）", "智云上海专线基础版（FTTR-B）"],
      requiredRisks: ["门店地址资源覆盖", "收银外卖连续性", "监控上传带宽"],
      forbidden: ["追问总部和分支组网", "按普通写字楼办公室场景推荐"],
    },
    rounds: [
      {
        topic: "门店规模",
        questions: ["这是单店还是多店？大概多少台收银、监控和 Wi-Fi 设备要同时在线？"],
        sampleReply: "单店，5台以内设备，收银和外卖打印必须稳定在线。",
      },
      {
        topic: "预算和开通",
        questions: ["预算更看重低成本还是稳定性？希望什么时候开通？"],
        sampleReply: "预算希望控制在3000元以内，最好一两周内开通。",
      },
      {
        topic: "可选能力",
        questions: ["是否还需要固定电话、门店宣传短视频上传或一条移动网络做备用？"],
        sampleReply: "暂时不要固定电话，监控云端上传，备用网络后续再看。",
      },
    ],
  },
  {
    id: "office_dynamic_ip",
    title: "小微办公室商企云宽",
    type: "上网-动态IP办公",
    minimumRounds: 0,
    query: "写字楼小公司 20 人日常办公上网，主要用 OA、企业微信、邮件和云文档，不需要服务器对外访问。",
    expected: {
      category: "internet_office_dynamic_ip_上网-中小企业动态IP办公",
      primaryProduct: "智云随选商企云宽或智云上海专线",
      alternatives: ["移动互联网宽带", "智享（沃快车）"],
      requiredRisks: ["带宽档位", "地址资源覆盖", "价格有效期"],
      forbidden: ["追问点对点或点对多组网", "默认客户需要固定公网 IP"],
    },
    rounds: [
      {
        topic: "办公规模",
        questions: ["大概有多少人同时办公？是否主要是网页、OA、企业微信、邮件和云文档？"],
        sampleReply: "20人左右，主要是普通办公和云文档协作。",
      },
      {
        topic: "固定IP边界",
        questions: [
          {
            text: "是否有官网或服务器需要长期通过同一个固定的网络地址供外部访问？",
            helper: "这种固定不变、可供外部访问的互联网地址通常叫固定公网 IP；普通办公上网不一定需要。",
          },
        ],
        sampleReply: "不需要固定公网IP，也没有服务器对外访问。",
      },
      {
        topic: "带宽预算",
        questions: ["期望带宽和预算大概是多少？如果不确定，可以按 100M/200M/500M 档位先估。"],
        sampleReply: "先按100M到200M看，预算一年1万元以内。",
      },
    ],
  },
  {
    id: "fixed_ip_boutique",
    title: "服务器2个固定IP",
    type: "上网-固定IP",
    minimumRounds: 0,
    query: "客户有官网和业务接口要对外访问，需要 2 个固定公网 IP，带宽希望上下行稳定。",
    expected: {
      category: "internet_fixed_ip_上网-固定IP上网",
      primaryProduct: "精品专线宽带",
      alternatives: ["沃专线", "智光（沃动车）"],
      requiredRisks: ["2个固定公网IP", "备案", "上行带宽和安全防护"],
      forbidden: ["追问总部和分支组网", "把固定IP归到组网类"],
    },
    rounds: [
      {
        topic: "IP数量和用途",
        questions: ["固定公网 IP 大概需要几个？主要用于官网、接口、邮件服务器还是其他系统？"],
        sampleReply: "需要2个，主要给官网和客户查询接口使用。",
      },
      {
        topic: "备案和安全",
        questions: ["域名备案是否已经完成？是否需要防火墙、DDoS 防护或端口开放控制？"],
        sampleReply: "域名已有，备案需要协助，需要基础防火墙和HTTPS端口开放。",
      },
      {
        topic: "带宽预算",
        questions: ["期望上下行带宽和预算范围是多少？"],
        sampleReply: "希望上下行50M以上，预算每月5000元以内。",
      },
    ],
  },
  {
    id: "fixed_ip_ipman",
    title: "多公网IP上云发布",
    type: "上网-IPMAN",
    minimumRounds: 0,
    query: "客户本地机房要发布多个系统，需要 32 个固定公网 IP，关注独享带宽和安全。",
    expected: {
      category: "internet_fixed_ip_上网-固定IP上网",
      primaryProduct: "IPMAN",
      alternatives: ["BGP&IPMAN", "精品专线"],
      requiredRisks: ["32个固定公网IP", "资源核实", "人工核价"],
      forbidden: ["推荐普通动态IP办公宽带", "追问点对点组网拓扑"],
    },
    rounds: [
      {
        topic: "IP规模",
        questions: ["需要多少个固定公网 IP？是否属于多个系统对外发布或大段地址需求？"],
        sampleReply: "需要32个固定公网IP，多个业务系统都要对外发布。",
      },
      {
        topic: "接入和带宽",
        questions: ["客户机房侧希望接入 100M、1G 还是更高？是否要求上下行对称独享？"],
        sampleReply: "希望1G口接入，带宽先按200M到500M看，要求对称独享。",
      },
      {
        topic: "开通核实",
        questions: ["安装地址、运营商偏好和上线时间是否明确？"],
        sampleReply: "在上海本地机房，优先电信，希望一个月内上线。",
      },
    ],
  },
  {
    id: "network_point_to_point",
    title: "两地内网互联",
    type: "组网-点对点",
    minimumRounds: 0,
    query: "上海总部和杭州仓库要做内网互通，两个固定地址之间需要稳定专线。",
    expected: {
      category: "network_point_to_point_组网-点对点",
      primaryProduct: "本地IPRAN、MSTP 或 OTN",
      alternatives: ["联通MSTP", "电路出租"],
      requiredRisks: ["两端地址资源", "带宽", "是否二层透明传送"],
      forbidden: ["按普通办公宽带推荐", "把国内两地访问识别成海外访问"],
    },
    rounds: [
      {
        topic: "站点和用途",
        questions: ["是否就是两个固定地址之间互联？两端分别在哪里，主要跑什么业务？"],
        sampleReply: "就是总部到仓库两个点，主要跑ERP和库存系统。",
      },
      {
        topic: "带宽可靠性",
        questions: ["每条线路大概需要多少带宽？更看重成本、低时延还是高可靠？"],
        sampleReply: "至少50M，业务比较关键，稳定性优先。",
      },
      {
        topic: "开通周期",
        questions: ["希望什么时候开通？两端是否已有运营商资源或机房弱电条件？"],
        sampleReply: "希望两个月内开通，两端都还没核实资源。",
      },
    ],
  },
  {
    id: "network_multipoint",
    title: "总部多分支MPLS",
    type: "组网-点对多",
    minimumRounds: 0,
    query: "客户总部要连接 5 个分支门店，要求内部系统稳定互通，不希望每个点都单独打通。",
    expected: {
      category: "network_point_to_multipoint_组网-点对多",
      primaryProduct: "MPLS-VPN 或 IPRAN 点对多",
      alternatives: ["MSTP", "OTN"],
      requiredRisks: ["中心点和分支点带宽", "拓扑结构", "运营商资源"],
      forbidden: ["只按门店上网推荐开店宝", "忽略总部中心化互联"],
    },
    rounds: [
      {
        topic: "拓扑结构",
        questions: ["是总部作为中心连接多个分支，还是各分支之间也要互通？"],
        sampleReply: "总部作为中心，5个分支都访问总部系统，分支之间不需要互通。",
      },
      {
        topic: "带宽和业务",
        questions: ["总部和分支分别需要多大带宽？主要承载 ERP、视频、语音还是文件传输？"],
        sampleReply: "总部100M，分支每点20M到50M，主要是ERP和文件。",
      },
      {
        topic: "资源和预算",
        questions: ["各站点城市或地址是否明确？预算和开通周期大概是多少？"],
        sampleReply: "都在长三角，预算每年15万元以内，希望两个月内开通。",
      },
    ],
  },
  {
    id: "network_smart",
    title: "多门店SD-WAN组网",
    type: "组网-智能组网",
    minimumRounds: 0,
    query: "连锁客户已有各门店宽带，想通过设备快速把多门店和总部系统连起来。",
    expected: {
      category: "network_smart_组网-智能组网",
      primaryProduct: "SD-WAN",
      alternatives: ["MSTP-VPN", "MPLS-VPN"],
      requiredRisks: ["已有宽带质量", "设备部署", "总部应用访问效果"],
      forbidden: ["要求客户必须重新拉传统专线", "只推荐单店宽带"],
    },
    rounds: [
      {
        topic: "站点规模",
        questions: ["一共有多少个门店要接入？总部系统在本地机房、云上还是第三方平台？"],
        sampleReply: "先接10个门店，总部系统在上海办公室。",
      },
      {
        topic: "已有线路",
        questions: ["门店现有宽带质量怎么样？是否接受在现有宽带上叠加设备组网？"],
        sampleReply: "各店已有宽带，质量参差不齐，希望先不大规模重新拉线。",
      },
      {
        topic: "业务要求",
        questions: ["主要访问收银后台、ERP、视频监控还是文件系统？对中断有没有特别敏感？"],
        sampleReply: "主要是收银后台和ERP，最好断网时能有备用线路。",
      },
    ],
  },
  {
    id: "clarify",
    title: "模糊网络方案咨询",
    type: "需求澄清",
    minimumRounds: 3,
    query: "客户只说想改善网络，没说是上网、固定 IP 还是多点互联。",
    expected: {
      category: "clarify_required",
      primaryProduct: "信息不足时先不推荐具体产品",
      alternatives: [],
      requiredRisks: ["先确认上网还是组网", "使用场景", "规模和预算"],
      forbidden: ["第一轮直接推荐产品", "一次追问太多问题"],
    },
    rounds: [
      {
        topic: "业务方向",
        questions: ["这次主要是办上网宽带、固定公网 IP，还是总部和分支之间组网互联？"],
        sampleReply: "主要是办公室上网，不是总部分支组网。",
      },
      {
        topic: "上网场景",
        questions: ["大概多少人使用？有没有官网或服务器需要固定公网 IP 对外访问？"],
        sampleReply: "30人办公，没有服务器对外访问，也不需要固定IP。",
      },
      {
        topic: "带宽预算",
        questions: ["预算和期望带宽大概是多少？如果不确定，可以先按 100M/200M/500M 估算。"],
        sampleReply: "预算一年1万元以内，先看200M左右的套餐。",
      },
    ],
  },
];
