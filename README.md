# Bank Card & Global Payment Network Simulator (`bank-card`)

用纯 Python 模拟 **虚拟银行卡（Virtual Visa/Mastercard）**、**四方支付清算模式（Four-Party Model）**、**Web3 U卡平台（Roogoo vs SuPay）** 以及 **Visa VAA 大数据反欺诈风控画像** 的教学推演项目。

## 核心模拟特性

1. **四方模式与多阶段资金流转**
   * **持卡人 (`User`)** $\rightarrow$ **商户 (`Merchant`，如 OpenAI)** $\rightarrow$ **收单行 (`Acquirer`，如 Stripe)** $\rightarrow$ **卡组织清算网 (`CardNetwork`，如 VisaNet)** $\rightarrow$ **上游发卡行 (`IssuerBank`)**。
   * **T+0 实时预授权冻结 (`Hold`)**、**T+1 跨行清算与三方手续费切蛋糕 (`Interchange Fee` / `Scheme Fee` / `Stripe Fee`)**、**T+2 商户对公银行提现 (`Payout`)**。
2. **两大虚拟卡平台流派对比 (`BaaS + BIN Sponsor`)**
   * **Roogoo（个人实名 KYC 消费 U 卡）**：0% 入金费、多种卡段（美国乐享卡、香港 3DS 尊享卡、质押 ROOG 0费率精英卡）、按笔加收消费手续费、余额不足拒付罚金与自动冻卡。
   * **SuPay（免 KYC 企业采购子卡分销台）**：仅邮箱注册、挂靠海外贸易公司母账户、充值前置扣 2% 手续费 + 较高开卡费、无个人 3DS 验证、同卡段遭羊毛党恶意刷单触发上游银行 **“全卡段连坐死卡”**。
3. **VisaNet AI 反欺诈引擎与《卡片历史行为画像库》 (`VAA`)**
   * 验证信息隔离：新开虚拟卡在首次刷卡前对 VisaNet 完全隐形；即使刷卡后，VisaNet 也不知道持卡人真名和卡内余额。
   * 报文路过 VisaNet 时毫秒级计算 **VAA 风险分 (`01~99`)**（检测 `$1` 小额探卡、跨国时空瞬移、消费额突变、高危 MCC），塞入 ISO 8583 报文提醒发卡行熔断拦截。

## 运行方式

本项目使用 `uv` 管理环境，直接运行：

```bash
uv run python -m bank_card
```
