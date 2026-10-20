import hashlib
import random
from bank_card.models import (
    AppleGiftCard,
    AuthRequest,
    AuthResponse,
    BaseIIClearingRecord,
    CardBehavioralProfile,
    CardTierConfig,
    InCommPOSARequest,
    MerchantStripeAccount,
    PassportDocument,
    PersonaVerificationResult,
    SAFEDeclaration,
    SwiftMT103Message,
    Transaction,
    VSSSettlementReport,
    VirtualCard,
)

REGION_NAMES = {
    "CN": "中国大陆",
    "HK": "中国香港",
    "PR": "美属波多黎各(离岸金融区)",
    "US": "美国本土",
    "JP": "日本",
    "RU": "俄罗斯",
    "IR": "伊朗(OFAC全面制裁国)",
    "KP": "朝鲜(OFAC全面制裁国)",
}


def calculate_luhn_check_digit(partial_15_digits: str) -> str:
    """根据前 15 位数字，使用 Luhn (模10) 算法计算第 16 位校验位"""
    total = 0
    for idx, ch in enumerate(reversed(partial_15_digits)):
        d = int(ch)
        if idx % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - (total % 10)) % 10)


def verify_luhn(card_number: str) -> bool:
    """校验 16 位银行卡号是否符合国际 ISO/IEC 7812 Luhn 模 10 算法"""
    return calculate_luhn_check_digit(card_number[:-1]) == card_number[-1]


class CardNetwork:
    """全球支付网络 / 卡组织（如 VisaNet：含 BASE I 实时授权路由与 BASE II 批量多边轧差清算引擎）"""

    SCHEME_FEE_RATE = 0.0015       # Visa 卡组织网络清算服务费率 (0.15%)
    INTERCHANGE_FEE_RATE = 0.0175  # 跨行交换费率 (1.75%，由收单行让渡给发卡行)

    def __init__(self, name: str = "VisaNet"):
        self.name = name
        self.bin_table: dict[str, "IssuerBank"] = {}
        self.behavioral_profiles: dict[str, CardBehavioralProfile] = {}
        self.base_ii_clearing_batch: list[BaseIIClearingRecord] = []
        self.visa_fedwire_settlement_pool_usd: float = 0.0  # Visa 在纽约美联储设立的中央对手方结算专户
        self.revenue: float = 0.0

    def register_issuer(self, bin_prefix: str, issuer: "IssuerBank") -> None:
        self.bin_table[bin_prefix] = issuer
        issuer.bin_prefix = bin_prefix
        region_cn = REGION_NAMES.get(issuer.region, issuer.region)
        print(
            f"[{self.name}] 已将 BIN [{bin_prefix}] ({region_cn}/{issuer.region} · {issuer.scheme} · {issuer.default_funding}) "
            f"分配给发卡行: {issuer.name}"
        )

    def get_issuer_by_bin(self, card_number: str) -> "IssuerBank | None":
        return self.bin_table.get(card_number[:6])

    @staticmethod
    def _hash_pan(card_number: str) -> str:
        return hashlib.sha256(card_number.encode()).hexdigest()[:12]

    def _evaluate_and_inject_vaa(self, req: AuthRequest) -> CardBehavioralProfile:
        pan_hash = self._hash_pan(req.card_number)
        masked = f"{req.card_number[:6]}******{req.card_number[-4:]}"
        profile = self.behavioral_profiles.get(pan_hash)

        if not profile:
            profile = CardBehavioralProfile(pan_hash=pan_hash, masked_pan=masked)
            self.behavioral_profiles[pan_hash] = profile
            print(
                f"  🧠 [{self.name} VAA 风控大脑] 首次观测到卡 {masked} 产生交易！"
                f"已新建行为画像 Key={pan_hash} (Visa 不知道持卡人姓名与余额)"
            )

        score = 10
        reasons: list[str] = []
        recent_1h = [t for t in profile.recent_tx_minutes if 0 <= (req.tx_minute - t) <= 60]

        if len(recent_1h) >= 2:
            score += 20
            reasons.append(f"1小时内高频交易(已刷{len(recent_1h)}次)")

        if req.amount <= 1.0:
            score += 15
            reasons.append("疑似$1小额探卡测试")
        elif profile.small_probe_count >= 1 and req.amount >= 50.0:
            score += 35
            reasons.append("高危模式:先$1小额探卡后立即大额消费")

        if profile.last_country and profile.last_country != req.merchant_country:
            delta_min = req.tx_minute - profile.last_tx_minute
            if delta_min <= 30:
                score += 45
                reasons.append(f"跨国时空瞬移({profile.last_country}->{req.merchant_country} 仅隔{delta_min}分钟)")

        if profile.approved_tx_count >= 1 and req.amount > profile.avg_amount * 5:
            score += 30
            reasons.append(f"金额突变(${req.amount:.0f} 远超历史均值 ${profile.avg_amount:.2f})")

        if req.mcc == "5999" and "5999" not in profile.usual_mccs:
            score += 15
            reasons.append("首次涉足高风险礼品卡/加密资产类目(MCC:5999)")

        req.vaa_score = min(score, 99)
        req.vaa_reasons = reasons if reasons else ["行为符合历史画像特征(正常)"]

        risk_level = "🔴极高危" if req.vaa_score >= 80 else ("🟡中风险" if req.vaa_score >= 40 else "🟢低风险")
        print(
            f"  🧠 [{self.name} VAA 评分注入] VAA 风险分: {req.vaa_score:02d}/99 ({risk_level}) "
            f"| 依据: {', '.join(req.vaa_reasons)}"
        )
        return profile

    def _update_profile_after_tx(self, profile: CardBehavioralProfile, req: AuthRequest, approved: bool) -> None:
        profile.total_tx_count += 1
        profile.recent_tx_minutes.append(req.tx_minute)
        profile.last_country = req.merchant_country
        profile.last_tx_minute = req.tx_minute
        if req.amount <= 1.0:
            profile.small_probe_count += 1

        if approved:
            prev_total = profile.avg_amount * profile.approved_tx_count
            profile.approved_tx_count += 1
            profile.avg_amount = round((prev_total + req.amount) / profile.approved_tx_count, 2)
            profile.usual_countries.add(req.merchant_country)
            profile.usual_mccs.add(req.mcc)

    def print_card_profiles(self) -> None:
        print(f"\n🔍 =================== 【{self.name} 大数据集群：卡片历史行为画像库】 ===================")
        if not self.behavioral_profiles:
            print("  (当前画像库为空：没有任何卡片发生过交易，VisaNet 甚至不知道市面上开了多少张卡)")
            return

        for pan_hash, p in self.behavioral_profiles.items():
            print(
                f"  📌 画像 Key(PAN Hash): {pan_hash} | 掩码卡号: {p.masked_pan}\n"
                f"     ├─ Visa 已知轨迹: 累计刷卡 {p.total_tx_count} 次 (成功 {p.approved_tx_count} 次) | "
                f"历史均额: ${p.avg_amount:.2f} | 常驻国: {sorted(p.usual_countries)} | 最近交易国: {p.last_country}(T={p.last_tx_minute}m)\n"
                f"     └─ Visa 未知盲区: 持卡人真实身份=[未知] | 卡内剩余可用余额=[未知] | 母账户总额度=[未知]"
            )

    def route_authorization(self, req: AuthRequest) -> AuthResponse:
        bin_prefix = req.card_number[:6]
        issuer = self.bin_table.get(bin_prefix)

        print(
            f"  -> [{self.name}] 收到 [{req.acquirer_name}] 授权请求 "
            f"(商户国:{req.merchant_country}, MCC:{req.mcc}, 时间:T+{req.tx_minute}m)"
        )
        if not issuer:
            return AuthResponse(approved=False, code="14", message="未知的卡号 BIN")

        profile = self._evaluate_and_inject_vaa(req)
        print(f"  -> [{self.name}] 携带 VAA 评分({req.vaa_score:02d}) 路由至发卡行 [{issuer.name} ({issuer.region})]...")
        resp = issuer.authorize(req)
        self._update_profile_after_tx(profile, req, resp.approved)
        return resp

    def receive_base_ii_batch(self, records: list[BaseIIClearingRecord]) -> None:
        """接收收单行在日终上传的 BASE II (TC05) 批量请款清分文件"""
        self.base_ii_clearing_batch.extend(records)

    def run_base_ii_multilateral_netting_and_settle(self, acquirer: "Acquirer") -> dict[str, VSSSettlementReport]:
        """
        【VisaNet BASE II 核心跑批系统：全球多边轧差 (Multilateral Netting) + VSS-110 报表下发 + 纽约 Fedwire 净头寸清算】
        核心铁律：
        1. 实时授权流水 (BASE I) != 最终清算账单 (BASE II)，未请款的 $1 探卡预授权绝不计入应付账款；
        2. 全球银行不需要两两互相打钱，Visa 作为【中央对手方 (Central Counterparty)】，将同一家银行的
           “发卡应付”、“收单应收”、“交换费分润 (+1.75%)”、“卡组织网络费 (-0.15%)”全部对冲轧差，
           在《VSS-110 报表》里给出一个唯一的【美元净头寸 (Net Settlement Position)】；
        3. 各家银行核对本地流水无误后，指令其存放在【美国纽约代理行 (Nostro Account)】里的真实美元，
           通过美国本土【Fedwire (美联储大额支付系统)】划入 Visa 纽约中央结算专户，再由 Visa 统一分发给收单行。
        """
        print(f"\n[清算第 2 步：{self.name} BASE II 超级计算机跑批 —— 全球多边轧差与下发《VSS-110 清算报表》]")
        reports: dict[str, VSSSettlementReport] = {}

        for bin_prefix, issuer in self.bin_table.items():
            # 统计该银行本地 EA Server 记录的 T+0 授权总额
            local_auth_total = round(sum(tx.amount for tx in issuer.local_auth_journal.values()), 2)

            # 筛选 BASE II 中属于该发卡行且商户已实际请款 (Captured) 的记录
            issuer_captured_records = [r for r in self.base_ii_clearing_batch if r.issuer_bin == bin_prefix]
            captured_auth_ids = {r.auth_id for r in issuer_captured_records}
            unmatched_auth_ids = [
                auth_id for auth_id in issuer.local_auth_journal if auth_id not in captured_auth_ids
            ]

            issuer_payable = round(sum(r.captured_usd for r in issuer_captured_records), 2)
            acquirer_receivable = round(issuer.inbound_acquiring_receivable_usd, 2)

            if local_auth_total == 0 and issuer_payable == 0 and acquirer_receivable == 0:
                continue

            # 计算交换费净收支：发卡行赚取 +1.75%，收单侧付出 -1.75%
            irf_earned = round(sum(r.interchange_fee_usd for r in issuer_captured_records), 2)
            irf_paid_on_acquiring = round(acquirer_receivable * self.INTERCHANGE_FEE_RATE, 2)
            interchange_net = round(irf_earned - irf_paid_on_acquiring, 2)

            # 计算应付 Visa 卡组织网络清算费 (0.15% * 双边清算总额)
            scheme_fee_issuer = round(sum(r.visa_scheme_fee_usd for r in issuer_captured_records), 2)
            scheme_fee_acquirer = round(acquirer_receivable * self.SCHEME_FEE_RATE, 2)
            total_scheme_fee = round(scheme_fee_issuer + scheme_fee_acquirer, 2)

            # 最终多边轧差净头寸：-发卡应付 + 收单应收 + 交换费净额 - Visa网络费
            net_position = round(-issuer_payable + acquirer_receivable + interchange_net - total_scheme_fee, 2)

            report = VSSSettlementReport(
                report_id=f"VSS-110-{bin_prefix}",
                member_bank_name=issuer.name,
                nostro_account_bic=issuer.nostro_bic,
                local_auth_total_usd=local_auth_total,
                base_ii_issuer_payable_usd=issuer_payable,
                base_ii_acquirer_receivable_usd=acquirer_receivable,
                interchange_fee_net_usd=interchange_net,
                visa_scheme_fee_usd=total_scheme_fee,
                net_settlement_position_usd=net_position,
                unmatched_auth_ids=unmatched_auth_ids,
            )
            reports[bin_prefix] = report

            direction = "应付 Visa 中央专户" if net_position < 0 else "应收 Visa 中央专户"
            print(
                f"  📄 [{self.name} 下发报表 {report.report_id}] 接收机构: 【{issuer.name}】\n"
                f"     ├─ 对比本地实时授权流水总额: ${local_auth_total:.2f} USD vs BASE II 实际请款总额: ${issuer_payable:.2f} USD\n"
                f"     ├─ 多边轧差明细: 发卡应付(-${issuer_payable:.2f}) + 境内收单冲抵(+${acquirer_receivable:.2f}) "
                f"+ 交换费净收支({interchange_net:+.2f}) - Visa网络费(-${total_scheme_fee:.2f})\n"
                f"     └─ 🎯 【VSS-110 核定最终美元净头寸】: {direction} ${abs(net_position):.2f} USD "
                f"(结算账户: {issuer.nostro_bic})"
            )

        # 第 3 步：各发卡行执行本地流水对账 (Reconciliation)、释放未请款预授权，并通过纽约 Fedwire 划拨净头寸
        print(f"\n[清算第 3 步：各发卡行本地 EA 流水自动对账 + 纽约代理行 (Nostro Account) Fedwire 头寸划拨]")
        for bin_prefix, report in reports.items():
            issuer = self.bin_table[bin_prefix]
            issuer_records = [r for r in self.base_ii_clearing_batch if r.issuer_bin == bin_prefix]
            issuer.reconcile_vss_and_wire_fedwire(report, issuer_records, self)

        # 同时如果存在境外银行（如美国 Chase）持卡人在招行境内 POS 刷卡的应收款，由境外发卡行划入 Visa 中央池
        for issuer in self.bin_table.values():
            if issuer.inbound_acquiring_receivable_usd > 0:
                gross_in = issuer.inbound_acquiring_receivable_usd
                chase_irf = round(gross_in * self.INTERCHANGE_FEE_RATE, 2)
                chase_scheme = round(gross_in * self.SCHEME_FEE_RATE, 2)
                chase_wire = round(gross_in - chase_irf + chase_scheme, 2)
                self.visa_fedwire_settlement_pool_usd = round(
                    self.visa_fedwire_settlement_pool_usd + chase_wire, 2
                )
                self.revenue = round(self.revenue + chase_scheme, 2)
                print(
                    f"  🏛️ [境外发卡行 JPMorgan Chase US · Fedwire 划入] 为来华游客在中国商户消费的 ${gross_in:.2f} USD "
                    f"向 Visa 纽约中央结算池划入净头寸 +${chase_wire:.2f} USD"
                )

        # 第 4 步：Visa 纽约中央专户扣除自身网络费后，通过 Fedwire 将收单净额划拨给 Stripe 收单行
        acquirer.receive_visa_fedwire_settlement(self.base_ii_clearing_batch, self)
        return reports


class IssuerBank:
    """持牌发卡银行（含境内/离岸核心账本 + 驻美国纽约代理行美元往来头寸池 Nostro Account）"""

    INTERCHANGE_FEE_RATE = 0.0175
    BIN_SUSPEND_THRESHOLD = 3
    VAA_BLOCK_THRESHOLD = 80

    def __init__(
        self,
        name: str,
        region: str,
        scheme: str = "Visa",
        default_funding: str = "credit",
        nostro_bic: str = "",
    ):
        self.name = name
        self.region = region
        self.scheme = scheme
        self.default_funding = default_funding
        self.nostro_bic = nostro_bic or (
            "CHASUS33 (驻纽约摩根大通美元往来账户 Nostro)"
            if region != "PR"
            else "FRNYUS33 (驻纽约联储准备金账户 Fed Account)"
        )
        self.bin_prefix: str = ""
        self.cards: dict[str, VirtualCard] = {}
        self.baas_platform = None
        self.revenue: float = 0.0
        self.bin_decline_count: int = 0
        self.bin_suspended: bool = False
        # 银行存放在美国本土银行体系内的真实美元头寸储备 (真美元永远留在美国银行体系内！)
        self.usd_nostro_balance: float = 500_000.0
        # 银行在境内的人民币资金池（用于接收国内信用卡持卡人的人民币自动购汇还款）
        self.cny_domestic_pool: float = 0.0
        # 发卡行自己机房 EA Server 记录的 T+0 本地授权流水日志 (auth_id -> Transaction)
        self.local_auth_journal: dict[str, Transaction] = {}
        # 该银行作为境内收单行（如外国游客在中国刷招行 POS 机）产生的应收美元，用于演示多边轧差
        self.inbound_acquiring_receivable_usd: float = 0.0
        # 信用卡已入账待还款的外币账单 (card_number -> billed_usd)
        self.posted_credit_bills_usd: dict[str, float] = {}

    def issue_direct_card(self, owner: str, initial_balance: float, card_name: str = "") -> VirtualCard:
        region_cn = REGION_NAMES.get(self.region, self.region)
        display_name = card_name or f"{self.name}·{self.scheme}卡({region_cn})"
        tier = CardTierConfig(
            tier_code=f"DIRECT_{self.region}",
            display_name=display_name,
            bin_prefix=self.bin_prefix,
            region=self.region,
            funding_type=self.default_funding,
            open_fee=0.0,
            tx_fee_rate=0.0,
            support_3ds=True,
        )
        card = self.create_card_record(
            owner=owner, user_label=owner, tier=tier, initial_balance=initial_balance
        )
        print(
            f"[{self.name} 直发卡成功] 持卡人: {owner} | 卡名: {display_name} | "
            f"卡号: {card.card_number} (BIN:{self.bin_prefix}·{self.region}·{self.default_funding} | Luhn:{verify_luhn(card.card_number)})"
        )
        return card

    def create_card_record(
        self,
        owner: str,
        user_label: str,
        tier: CardTierConfig,
        initial_balance: float,
        custom_card_number: str | None = None,
    ) -> VirtualCard:
        if custom_card_number:
            card_number = custom_card_number.replace(" ", "")
        else:
            random_9_digits = "".join(str(random.randint(0, 9)) for _ in range(9))
            partial_15 = f"{self.bin_prefix}{random_9_digits}"
            check_digit = calculate_luhn_check_digit(partial_15)
            card_number = f"{partial_15}{check_digit}"

        cvv = f"{random.randint(100, 999)}"
        card = VirtualCard(
            card_number=card_number,
            cvv=cvv,
            expiry="10/29",
            owner=owner,
            user_label=user_label,
            tier=tier,
            available_balance=initial_balance,
        )
        self.cards[card_number] = card
        return card

    def authorize(self, req: AuthRequest) -> AuthResponse:
        if self.bin_suspended:
            return AuthResponse(approved=False, code="62", message=f"卡段 [{self.bin_prefix}] 已被发卡行整体封停")

        card = self.cards.get(req.card_number)
        if not card:
            return AuthResponse(approved=False, code="14", message="卡号不存在")

        if card.cvv != req.cvv or card.expiry != req.expiry:
            return AuthResponse(approved=False, code="N7", message="CVV 或有效期校验失败")

        if req.vaa_score >= self.VAA_BLOCK_THRESHOLD:
            print(
                f"  🛡️ [{self.name} 反欺诈熔断] 检测到报文中 Visa VAA 评分高达 {req.vaa_score}/99 "
                f"(阈值 {self.VAA_BLOCK_THRESHOLD})！\n"
                f"     命中高危特征: {req.vaa_reasons}\n"
                f"     尽管 CVV({req.cvv})正确且卡内余额(${card.available_balance:.2f})充足，"
                f"发卡行仍以 [59 疑似欺诈] 紧急拦截此交易！"
            )
            return AuthResponse(
                approved=False,
                code="59",
                message=f"发卡行反欺诈拦截 (VAA={req.vaa_score}: {', '.join(req.vaa_reasons)})",
            )

        if req.require_3ds:
            if not card.tier.support_3ds:
                return AuthResponse(approved=False, code="3D", message="卡片不支持 3DS 验证")
            print(f"  -> [{self.name} 3DS 验证通过] 持卡人 [{card.user_label}] 已完成 3DS 动态码验证")

        if self.baas_platform:
            resp = self.baas_platform.handle_auth_webhook(card, req)
            if resp.approved:
                self.local_auth_journal[resp.auth_id] = Transaction(
                    auth_id=resp.auth_id,
                    card_number=req.card_number,
                    merchant_name=req.merchant_name,
                    amount=req.amount,
                    is_auth_only_probe=(req.amount <= 1.0),
                )
            return resp

        if card.available_balance < req.amount:
            return AuthResponse(approved=False, code="51", message="银行卡可用额度不足")

        card.available_balance = round(card.available_balance - req.amount, 2)
        card.frozen_balance = round(card.frozen_balance + req.amount, 2)
        auth_id = f"AUTH-{random.randint(1000, 9999)}"
        self.local_auth_journal[auth_id] = Transaction(
            auth_id=auth_id,
            card_number=req.card_number,
            merchant_name=req.merchant_name,
            amount=req.amount,
            is_auth_only_probe=(req.amount <= 1.0),
        )
        print(
            f"  <- [{self.name} 授权通过({auth_id})] 传统银行直联扣款冻结 ${req.amount:.2f} "
            f"(0附加手续费 | 剩余额度: ${card.available_balance:.2f})"
        )
        return AuthResponse(approved=True, code="00", message="Approved", auth_id=auth_id)

    def record_inbound_foreign_card_acquiring(
        self, foreign_tourist: str, foreign_bank: str, domestic_merchant: str, amount_usd: float
    ) -> None:
        """模拟外国游客持境外 Visa 卡在中国境内刷本行 POS 机，形成本行对 VisaNet 的【应收美元头寸】"""
        self.inbound_acquiring_receivable_usd = round(self.inbound_acquiring_receivable_usd + amount_usd, 2)
        print(
            f"  🌐 [{self.name} · 境内 POS 外卡收单] 外国游客 [{foreign_tourist}] 持 [{foreign_bank}] Visa 卡\n"
            f"     在境内商户【{domestic_merchant}】刷卡消费折合 ${amount_usd:.2f} USD ➔ 记入本行应收 Visa 清算款 +${amount_usd:.2f} USD"
        )

    def reconcile_vss_and_wire_fedwire(
        self,
        report: VSSSettlementReport,
        captured_records: list[BaseIIClearingRecord],
        visa_net: CardNetwork,
    ) -> None:
        """
        发卡行收到 Visa VSS-110 报表后的自动化处理：
        1. 将本地 EA Server 授权流水日志与 BASE II 实际请款记录逐笔勾兑 (Reconciliation)；
        2. 对已请款交易：将冻结额度转为正式入账账单；对商户未请款的 $1 探卡预授权：自动解冻释放回可用余额；
        3. 指令本行设在美国纽约的代理行账户 (Nostro Account)，通过 Fedwire 向 Visa 纽约中央结算账户划拨美元净头寸。
        """
        print(f"  🔍 [{self.name} · 本地对账系统] 正在将本地 EA 授权流水与 [{report.report_id}] 逐笔勾兑...")
        for rec in captured_records:
            local_tx = self.local_auth_journal.get(rec.auth_id)
            card = self.cards[rec.card_number]
            card.frozen_balance = round(card.frozen_balance - rec.captured_usd, 2)
            if local_tx:
                local_tx.settled = True
            if card.tier.funding_type == "credit":
                self.posted_credit_bills_usd[card.card_number] = round(
                    self.posted_credit_bills_usd.get(card.card_number, 0.0) + rec.captured_usd, 2
                )
            print(
                f"     ├─ ✅ 勾兑成功 [{rec.auth_id} <-> {rec.clearing_id}]: 商户 [{rec.merchant_name}] "
                f"请款 ${rec.captured_usd:.2f} USD (本行赚取交换费 +${rec.interchange_fee_usd:.2f} USD)"
            )

        # 处理本地已授权冻结、但商户在 BASE II 中未实际请款的交易（如黑客 $1 小额探卡）
        for unmatched_id in report.unmatched_auth_ids:
            unmatched_tx = self.local_auth_journal[unmatched_id]
            card = self.cards[unmatched_tx.card_number]
            card.frozen_balance = round(card.frozen_balance - unmatched_tx.amount, 2)
            card.available_balance = round(card.available_balance + unmatched_tx.amount, 2)
            print(
                f"     ├─ 🔓 识别出未请款预授权 [{unmatched_id}]: 商户 [{unmatched_tx.merchant_name}] 未对 ${unmatched_tx.amount:.2f} 发起 BASE II 请款！"
                f"已自动解冻并退回持卡人可用余额 (当前可用: ${card.available_balance:.2f})"
            )

        self.revenue = round(self.revenue + report.interchange_fee_net_usd - report.visa_scheme_fee_usd, 2)

        # 通过美国纽约代理行 (Nostro Account) & Fedwire 划拨轧差后的净美元头寸
        if report.net_settlement_position_usd < 0:
            wire_usd = abs(report.net_settlement_position_usd)
            self.usd_nostro_balance = round(self.usd_nostro_balance - wire_usd, 2)
            visa_net.visa_fedwire_settlement_pool_usd = round(
                visa_net.visa_fedwire_settlement_pool_usd + wire_usd, 2
            )
            visa_net.revenue = round(visa_net.revenue + report.visa_scheme_fee_usd, 2)
            print(
                f"     └─ 💸 [纽约 Fedwire 头寸调拨] [{self.name}] 指令【{self.nostro_bic}】\n"
                f"        划出轧差净头寸 -${wire_usd:.2f} USD ➔ 【Visa 纽约中央结算专户】 "
                f"(本行纽约 Nostro 剩余美元头寸: ${self.usd_nostro_balance:,.2f} USD)"
            )

    def auto_repay_credit_bill_in_cny(self, card: VirtualCard, spot_fx_rate: float = 7.2500) -> None:
        """
        【解答：为什么刷国内银行 Visa 信用卡不需要提前存美金？】
        1. 刷卡次日 (T+1)，银行已经动用其存放在【美国纽约代理行 (Nostro Account)】的真美元替你和 Visa 结清了账单；
        2. 到了国内账单还款日，你只需直接还人民币！银行开启【人民币自动购汇还款】，按当日牌价扣取你的人民币，
           收进银行境内人民币资金池，冲平你的美元信用卡欠款。
        """
        billed_usd = self.posted_credit_bills_usd.get(card.card_number, 0.0)
        if billed_usd <= 0:
            return

        cny_needed = round(billed_usd * spot_fx_rate, 2)
        self.cny_domestic_pool = round(self.cny_domestic_pool + cny_needed, 2)
        self.posted_credit_bills_usd[card.card_number] = 0.0
        card.available_balance = round(card.available_balance + billed_usd, 2)

        print(
            f"\n[清算第 5 步：国内持卡人账单日“人民币自动购汇还款”闭环 (不占个人 5 万美元购汇额度)]\n"
            f"  💳 [{self.name} · 自动购汇还款] 持卡人 [{card.owner}] 信用卡外币已入账账单: ${billed_usd:.2f} USD\n"
            f"     ├─ 持卡人操作: 无需提前存美金！直接从 [{card.owner}] 境内人民币储蓄卡扣取 ¥{cny_needed:.2f} CNY (@牌价 {spot_fx_rate:.4f})\n"
            f"     ├─ 银行内部平盘: [{self.name}] 境内人民币资金池 +¥{cny_needed:.2f} CNY (补偿此前纽约 Nostro 账户垫付的美元头寸)\n"
            f"     └─ ✅ 账单结清！卡片信用额度已恢复至: ${card.available_balance:.2f} USD"
        )


class RoogooPlatform:
    """Roogoo 平台（个人实名 KYC 消费 U 卡，带手机 App 交易账单日志）"""

    CARD_TIERS: dict[str, CardTierConfig] = {
        "ENJOY": CardTierConfig(
            tier_code="ENJOY",
            display_name="Roogoo·乐享卡(真实卡段454924·波多黎各Prepaid预付卡)",
            bin_prefix="454924",
            region="PR",
            funding_type="prepaid",
            open_fee=0.0,
            tx_fee_rate=0.01,
            decline_fee=0.50,
            support_3ds=False,
            require_kyc=True,
        ),
        "PREMIER": CardTierConfig(
            tier_code="PREMIER",
            display_name="Roogoo·尊享卡(香港538720·Prepaid预付卡)",
            bin_prefix="538720",
            region="HK",
            funding_type="prepaid",
            open_fee=4.99,
            tx_fee_rate=0.016,
            decline_fee=1.00,
            support_3ds=True,
            require_kyc=True,
        ),
    }

    MAX_DECLINE_LIMIT = 2

    def __init__(self, name: str = "Roogoo 平台"):
        self.name = name
        self.sponsor_banks: dict[str, IssuerBank] = {}
        self.user_usd_wallets: dict[str, float] = {}
        self.kyc_users: set[str] = set()
        self.revenue: float = 0.0
        # 模拟用户在 Roogoo 手机 App 里能看到的卡片交易流水（只有请求真正到达 Roogoo 才会留下记录！）
        self.app_transaction_logs: dict[str, list[str]] = {}

    def connect_sponsor_bank(self, bank: IssuerBank) -> None:
        self.sponsor_banks[bank.bin_prefix] = bank
        bank.baas_platform = self

    def complete_kyc(self, user_name: str) -> None:
        self.kyc_users.add(user_name)

    def deposit_usdt(self, user_name: str, usdt_amount: float) -> None:
        self.user_usd_wallets[user_name] = round(self.user_usd_wallets.get(user_name, 0.0) + usdt_amount, 2)

    def open_card(
        self,
        user_name: str,
        tier_code: str,
        load_amount: float,
        custom_card_number: str | None = None,
    ) -> VirtualCard:
        tier = self.CARD_TIERS[tier_code]
        total_cost = round(tier.open_fee + load_amount, 2)
        self.user_usd_wallets[user_name] = round(self.user_usd_wallets[user_name] - total_cost, 2)
        self.revenue = round(self.revenue + tier.open_fee, 2)

        sponsor_bank = self.sponsor_banks[tier.bin_prefix]
        card = sponsor_bank.create_card_record(
            owner=user_name,
            user_label=user_name,
            tier=tier,
            initial_balance=load_amount,
            custom_card_number=custom_card_number,
        )
        self.app_transaction_logs.setdefault(card.card_number, []).append(
            f"[开卡充值] 初始转入 +${load_amount:.2f} | 当前可用余额: ${card.available_balance:.2f}"
        )
        print(
            f"[{self.name} 开卡成功] 持卡人: {card.owner} | 卡种: {tier.display_name}\n"
            f"  └─ 卡号: {card.card_number} (6位BIN:{card.card_number[:6]} | 地区:{tier.region} | "
            f"属性:{tier.funding_type} | Luhn:{verify_luhn(card.card_number)}) | 余额: ${card.available_balance:.2f}"
        )
        return card

    def handle_auth_webhook(self, card: VirtualCard, req: AuthRequest) -> AuthResponse:
        logs = self.app_transaction_logs.setdefault(card.card_number, [])
        if card.is_frozen:
            logs.append(f"[交易被拒·卡片已冻结] 商户: {req.merchant_name} | 金额: ${req.amount:.2f}")
            return AuthResponse(approved=False, code="62", message="卡片已被风控冻结")

        roogoo_tx_fee = round(req.amount * card.tier.tx_fee_rate, 2)
        total_needed = round(req.amount + roogoo_tx_fee, 2)

        if card.available_balance < total_needed:
            card.decline_count += 1
            penalty = min(card.available_balance, card.tier.decline_fee)
            card.available_balance = round(card.available_balance - penalty, 2)
            self.revenue = round(self.revenue + penalty, 2)
            if card.decline_count >= self.MAX_DECLINE_LIMIT:
                card.is_frozen = True
            logs.append(
                f"[❌ 交易失败·余额不足] 商户: {req.merchant_name} (${req.amount:.2f}) | "
                f"扣除拒付罚金: -${penalty:.2f} | 余额: ${card.available_balance:.2f}"
            )
            return AuthResponse(approved=False, code="51", message=f"余额不足，已扣拒付罚金 ${penalty:.2f}")

        card.decline_count = 0
        card.available_balance = round(card.available_balance - total_needed, 2)
        card.frozen_balance = round(card.frozen_balance + req.amount, 2)
        self.revenue = round(self.revenue + roogoo_tx_fee, 2)

        auth_id = f"AUTH-{random.randint(1000, 9999)}"
        logs.append(
            f"[✅ 消费授权成功({auth_id})] 商户: {req.merchant_name} | 消费: -${req.amount:.2f} | "
            f"手续费: -${roogoo_tx_fee:.2f} | 余额: ${card.available_balance:.2f}"
        )
        print(
            f"  <- [{self.name} 授权通过({auth_id})] 冻结消费 ${req.amount:.2f} + "
            f"平台加收手续费({card.tier.tx_fee_rate * 100:.1f}%) ${roogoo_tx_fee:.2f} | 剩: ${card.available_balance:.2f}"
        )
        return AuthResponse(approved=True, code="00", message="Approved", auth_id=auth_id)

    def print_app_bill(self, card: VirtualCard) -> None:
        """模拟用户打开 Roogoo 手机 App 查看该卡的交易记录与拒付费扣除情况"""
        print(f"\n📱 --- 【用户打开 Roogoo 手机 App 查看卡 {card.card_number} 账单明细】 ---")
        for idx, entry in enumerate(self.app_transaction_logs.get(card.card_number, []), 1):
            print(f"  {idx}. {entry}")
        print(
            "  💡 [App 账单结论] 没有任何来自 Anthropic Claude Code 的失败交易记录，也未扣 $0.50 拒付费！\n"
            "     原因：请求在第一关 (Stripe Radar) 就被短路拦截了，根本没有穿过 VisaNet 到达 Roogoo 服务器！"
        )


class StripeRadar:
    """
    【Stripe Radar 独立风控引擎】
    部署在 Stripe 收单行最前端（进入 VisaNet 之前）。
    它自带全球 BIN 本地缓存表，可在几毫秒内直接识别：
    - 发卡国家/地区 (CN / HK / PR 波多黎各 / US 美国本土)
    - 卡片资金类型 (credit 信用卡 / debit 借记卡 / prepaid 预付充值卡)
    - 高风险虚拟卡 BIN 黑名单 (如 454924 等高频用于 AI 订阅的 U 卡号段)
    一旦命中拦截规则，直接在 Stripe 层短路返回 "Your card has been declined."，不发往 VisaNet！
    """

    def inspect(self, merchant: "Merchant", card_number: str, issuer: IssuerBank | None) -> tuple[bool, str]:
        if not issuer:
            return False, "Your card has been declined. (invalid_number)"

        bin_prefix = card_number[:6]
        card_region = issuer.region
        region_cn = REGION_NAMES.get(card_region, card_region)
        funding_type = issuer.default_funding

        # 1. 检查发卡国家/地区政策禁入 (如 CN、HK、RU)
        if merchant.blocked_regions and card_region in merchant.blocked_regions:
            print(
                f"  🚫 [Stripe Radar · 地区政策短路拦截] 本地查表 BIN={bin_prefix} 属于 [{issuer.name}]，发卡地为 【{region_cn} ({card_region})】！\n"
                f"     命中规则: 商户 [{merchant.name}] 禁止接收 {merchant.blocked_regions} 地区银行卡 (内部原因码: country_not_supported)"
            )
            return False, f"Your card has been declined. (政策限制: 不支持 {region_cn}[{card_region}] 发行的银行卡)"

        # 2. 检查是否命中商户拉黑的高频虚拟卡 BIN 黑名单 (如 Roogoo 的 454924)
        if merchant.blacklisted_bins and bin_prefix in merchant.blacklisted_bins:
            print(
                f"  🚫 [Stripe Radar · 虚拟卡 BIN 黑名单短路拦截] 本地查表识别出 BIN={bin_prefix} ({issuer.name} · {region_cn})\n"
                f"     命中规则 1: BIN [{bin_prefix}] 已被标记为高频 Web3 虚拟卡号段 (high_risk_virtual_card_bin)\n"
                f"     命中规则 2: 卡片资金属性为 '{funding_type}' (Prepaid 预付卡)，且发卡地为离岸金融区 '{card_region}' 而非美国本土 'US'\n"
                f"     ⚡ 动作: Stripe Radar 直接在收单层掐断请求（不发往 VisaNet，也不通知 Roogoo），向网页前端返回笼统报错: 'Your card has been declined.'"
            )
            return False, "Your card has been declined."

        # 3. 检查是否开启了一刀切拦截 Prepaid 预付卡
        if merchant.block_prepaid and funding_type == "prepaid":
            print(
                f"  🚫 [Stripe Radar · 预付卡属性短路拦截] 识别到 BIN={bin_prefix} 的资金属性为 【Prepaid (预付卡)】！\n"
                f"     命中规则: Block if ::card_funding:: == 'prepaid'"
            )
            return False, "Your card has been declined."

        print(
            f"  ✅ [Stripe Radar · 预检放行] BIN={bin_prefix} ({region_cn}·{funding_type}) 通过收单层风控，准备发往 VisaNet..."
        )
        return True, "OK"


class Acquirer:
    """收单机构（如 Stripe，内置 StripeRadar 风控引擎）"""

    def __init__(self, name: str, network: CardNetwork):
        self.name = name
        self.network = network
        self.radar = StripeRadar()
        self.merchant_accounts: dict[str, MerchantStripeAccount] = {}
        self.pending_transactions: list[Transaction] = []

    def register_merchant(self, merchant: "Merchant") -> None:
        self.merchant_accounts[merchant.name] = MerchantStripeAccount(merchant_name=merchant.name)
        rules = []
        if merchant.blocked_regions:
            rules.append(f"禁入地区:{merchant.blocked_regions}")
        if merchant.block_prepaid:
            rules.append("拦截Prepaid预付卡")
        if merchant.blacklisted_bins:
            rules.append(f"封禁高危虚拟卡BIN:{merchant.blacklisted_bins}")
        rule_str = " | ".join(rules) if rules else "全球无限制"
        print(f"[{self.name}] 已为商户 [{merchant.name}] 开通收单服务 | Stripe Radar 规则: {rule_str}")

    def process_payment(
        self,
        merchant: "Merchant",
        card_number: str,
        cvv: str,
        expiry: str,
        amount: float,
        tx_minute: int = 0,
    ) -> AuthResponse:
        issuer = self.network.get_issuer_by_bin(card_number)

        # 第一关：先过 Stripe Radar 本地风控（如果被拦截，直接短路返回，绝不触碰 VisaNet 和发卡行！）
        passed, decline_msg = self.radar.inspect(merchant, card_number, issuer)
        if not passed:
            return AuthResponse(approved=False, code="RADAR_BLOCK", message=decline_msg)

        # 第二关：通过 Stripe Radar 后，才打包 ISO 8583 报文发往 VisaNet 和上游发卡行
        req = AuthRequest(
            card_number=card_number,
            cvv=cvv,
            expiry=expiry,
            amount=amount,
            merchant_name=merchant.name,
            acquirer_name=self.name,
            merchant_country=merchant.country,
            mcc=merchant.mcc,
            tx_minute=tx_minute,
            require_3ds=merchant.require_3ds,
        )
        resp = self.network.route_authorization(req)

        if resp.approved:
            account = self.merchant_accounts[merchant.name]
            account.pending_balance = round(account.pending_balance + amount, 2)
            self.pending_transactions.append(
                Transaction(
                    auth_id=resp.auth_id,
                    card_number=card_number,
                    merchant_name=merchant.name,
                    amount=amount,
                    is_auth_only_probe=(amount <= 1.0),
                )
            )
        else:
            print(f"  <- [{self.name}] 收到发卡行拒绝回执：[{resp.code}] {resp.message}")

        return resp

    def submit_base_ii_clearing_batch(self) -> list[BaseIIClearingRecord]:
        """
        【日终 T+1 第一步：收单行向 VisaNet BASE II 系统上传《TC05 批量请款清分文件》】
        为什么不能直接按 T+0 授权流水结账？
        因为：
        1. 酒店押金、租车预授权、或者 $1.00 绑卡/黑客探卡测试 (Auth-Only)，商户最终不会发货也不会请款 (Not Captured)；
        2. 只有商户确认交货并调用 Capture 接口的交易，收单行才会打包进 BASE II (TC05) 清分批次文件送交 VisaNet。
        """
        print(f"\n[清算第 1 步：{self.name} 日终打包上传《BASE II (TC05) 批量请款清分文件》]")
        batch: list[BaseIIClearingRecord] = []

        for tx in self.pending_transactions:
            if tx.settled:
                continue

            if tx.is_auth_only_probe:
                # $1.00 探卡/预授权验证：商户不请款，从收单行待结算池剔除
                acct = self.merchant_accounts[tx.merchant_name]
                acct.pending_balance = round(acct.pending_balance - tx.amount, 2)
                tx.settled = True
                print(
                    f"  ⏭️ [{self.name} · 剔除未请款预授权] 授权单 [{tx.auth_id}] ({tx.merchant_name} ${tx.amount:.2f}) "
                    f"属于 $1 小额探卡/预授权 (Auth-Only)，商户未发起 Capture 请款，不纳入 BASE II 清算！"
                )
                continue

            irf_usd = round(tx.amount * self.network.INTERCHANGE_FEE_RATE, 2)
            scheme_usd = round(tx.amount * self.network.SCHEME_FEE_RATE, 2)
            rec = BaseIIClearingRecord(
                clearing_id=f"TC05-{random.randint(10000, 99999)}",
                auth_id=tx.auth_id,
                card_number=tx.card_number,
                issuer_bin=tx.card_number[:6],
                merchant_name=tx.merchant_name,
                acquirer_name=self.name,
                authorized_usd=tx.amount,
                captured_usd=tx.amount,
                interchange_fee_usd=irf_usd,
                visa_scheme_fee_usd=scheme_usd,
            )
            tx.settled = True
            batch.append(rec)
            print(
                f"  📦 [{self.name} 打包 TC05 清分单 {rec.clearing_id}] 关联授权:{rec.auth_id} | "
                f"发卡BIN:{rec.issuer_bin} | 商户:{rec.merchant_name} | 实际请款: ${rec.captured_usd:.2f} USD"
            )

        self.network.receive_base_ii_batch(batch)
        return batch

    def receive_visa_fedwire_settlement(
        self, records: list[BaseIIClearingRecord], visa_net: CardNetwork
    ) -> None:
        """收到 Visa 纽约中央结算专户通过 Fedwire 划来的收单结算净额，并分账至各商户可用余额"""
        gross_sales = round(sum(r.captured_usd for r in records), 2)
        total_irf_deducted = round(sum(r.interchange_fee_usd for r in records), 2)
        acquirer_scheme_fee = round(sum(r.visa_scheme_fee_usd for r in records), 2)
        net_from_visa = round(gross_sales - total_irf_deducted - acquirer_scheme_fee, 2)

        visa_net.visa_fedwire_settlement_pool_usd = round(
            visa_net.visa_fedwire_settlement_pool_usd - net_from_visa, 2
        )
        visa_net.revenue = round(visa_net.revenue + acquirer_scheme_fee, 2)

        print(
            f"\n[清算第 4 步：Visa 纽约中央专户 ➔ {self.name} Fedwire 入账与商户分账]\n"
            f"  🏦 [{self.name}] 收到【Visa 纽约中央结算专户】Fedwire 划入净结算款: +${net_from_visa:.2f} USD\n"
            f"     (计算公式: 商户请款总额 ${gross_sales:.2f} - 让渡给发卡行的交换费 ${total_irf_deducted:.2f} "
            f"- 支付 Visa 网络费 ${acquirer_scheme_fee:.2f})"
        )

        for r in records:
            acct = self.merchant_accounts[r.merchant_name]
            stripe_fee = round(r.captured_usd * 0.029 + 0.30, 2)
            merchant_net = round(r.captured_usd - stripe_fee, 2)
            acct.pending_balance = round(acct.pending_balance - r.captured_usd, 2)
            acct.available_balance = round(acct.available_balance + merchant_net, 2)
            print(
                f"     ├─ 商户 [{r.merchant_name}] 结算入账: 原始订单 ${r.captured_usd:.2f} "
                f"- Stripe 收单费(2.9%+$0.30) ${stripe_fee:.2f} ➔ 可提现余额 +${merchant_net:.2f} USD"
            )



class SAFEForeignExchangeBureau:
    """
    【国家外汇管理局 (SAFE) · 跨境外汇支付业务监测系统】
    依据《国家外汇管理局关于规范跨境电子商务外汇支付业务的通知》(汇发〔2019〕13号)：
    1. 严禁支付机构只申报一个笼统的“境外汇款总包”，必须执行【逐笔还原申报 (Transaction Restoration)】；
    2. 核验“三单合一”：境内买家实名身份证 (`buyer_id_card`) + 境外商户真实子订单号 (`sub_order_id`) + 资金流水；
    3. 核验国际收支交易编码 (`bop_code = "122030"` 代表经常项目-网络购物/数字商品贸易) 与单笔限额；
    4. 核验通过后签发电子核准令牌 (`SAFE-PERMIT-...`)，境内清算银行必须凭此令牌才允许将人民币购汇为美元汇出境外。
    """

    SINGLE_TX_LIMIT_USD = 50000.0  # 跨境电商外汇支付单笔上限 ($50,000)

    def __init__(self):
        self.declarations_ledger: list[SAFEDeclaration] = []
        self.citizen_fx_ledgers: dict[str, float] = {}  # 身份证号 -> 累计跨境消费购汇额 (USD)

    def submit_restoration_declaration(self, decl: SAFEDeclaration) -> str:
        """审核支付机构报送的《逐笔还原申报单》，通过后签发电子准购令牌"""
        if decl.fx_usd_amount > self.SINGLE_TX_LIMIT_USD:
            raise ValueError(f"超出外管局单笔跨境消费限额 ${self.SINGLE_TX_LIMIT_USD}")

        decl.approved_token = f"SAFE-PERMIT-{random.randint(100000, 999999)}"
        self.declarations_ledger.append(decl)
        self.citizen_fx_ledgers[decl.buyer_id_card] = round(
            self.citizen_fx_ledgers.get(decl.buyer_id_card, 0.0) + decl.fx_usd_amount, 2
        )

        print(
            f"  🏛️ [国家外管局 SAFE · 还原申报核验] 收到 [{decl.payment_institution}] 报送单 [{decl.declaration_no}]\n"
            f"     ├─ 实名主体穿透: 买家={decl.buyer_name} (身份证:{decl.buyer_id_card}) <-> 还原境外子订单={decl.sub_order_id}\n"
            f"     ├─ 国际收支编码: BOP={decl.bop_code} (跨境电商经常项目消费) | 申报购汇: ¥{decl.cny_amount:.2f} CNY ➔ ${decl.fx_usd_amount:.2f} USD\n"
            f"     └─ ✅ 三单合一核验通过！签发外管局准购电子令牌: 【{decl.approved_token}】"
        )
        return decl.approved_token


class OnshoreClearingBank:
    """
    【境内合作外汇清算银行 (如：中国银行境内分行 BKCHCNBJ)】
    为什么支付宝不能自己把人民币变成美元？
    因为支付宝是“非银行支付机构”，没有银行间外汇市场做市商席位，也没有海外美元清算头寸。
    必须由境内清算银行：
    1. 校验外管局签发的 `safe_approval_token`（无外管局令牌严禁售汇）；
    2. 收取支付宝备付金专户划来的人民币 (`¥145.00 CNY`)，按银行即期外汇牌价 (`7.2500`) 执行【代客即期售汇】转换成 `$20.00 USD`；
    3. 扣除跨境清算与 SWIFT 电报费 (`$0.30 USD`)，从本行驻纽约代理行的美元往账池 (`usd_nostro_pool`) 划出 `$19.70 USD`，
       发送真实的 `SWIFT MT103` 跨境电文至纽约摩根大通银行 (`CHASUS33`)。
    """

    SPOT_ASK_RATE = 7.2500          # 银行美元现汇卖出价 (1 USD = 7.2500 CNY)
    CORRESPONDENT_FEE_USD = 0.30    # 跨境清算及 SWIFT 电报手续费 ($0.30)

    def __init__(self, name: str = "中国银行境内跨境清算中心", bic: str = "BKCHCNBJ"):
        self.name = name
        self.bic = bic
        self.cny_fx_pool: float = 0.0             # 银行吸收的购汇人民币资金池 (+¥145.00)
        self.usd_nostro_pool: float = 1_000_000.0 # 银行存放于纽约代理行的美元往账头寸池 (Nostro Account)
        self.fx_fee_revenue_usd: float = 0.0      # 银行与跨境通道留存的清算手续费 (+$0.30)
        self.swift_mt103_log: list[SwiftMT103Message] = []

    def execute_spot_fx_and_swift_wire(
        self,
        cny_received: float,
        target_usd: float,
        safe_approval_token: str,
        receiver_bic: str,
        beneficiary_name: str,
    ) -> SwiftMT103Message:
        """凭外管局准购令牌执行代客即期购汇，并向境外收款行发送 SWIFT MT103 报文"""
        if not safe_approval_token.startswith("SAFE-PERMIT-"):
            raise PermissionError("缺少国家外管局 (SAFE) 准购令牌，境内银行拒绝办理跨境购汇及汇出！")

        # 1. 吸收人民币备付金，按即期牌价售出美元
        self.cny_fx_pool = round(self.cny_fx_pool + cny_received, 2)
        gross_usd = round(cny_received / self.SPOT_ASK_RATE, 2)
        assert abs(gross_usd - target_usd) < 0.01

        # 2. 扣除跨境清算费 $0.30，从纽约美元往账头寸池划出净额 $19.70
        self.fx_fee_revenue_usd = round(self.fx_fee_revenue_usd + self.CORRESPONDENT_FEE_USD, 2)
        net_settled_usd = round(gross_usd - self.CORRESPONDENT_FEE_USD, 2)
        self.usd_nostro_pool = round(self.usd_nostro_pool - net_settled_usd, 2)

        mt103 = SwiftMT103Message(
            uetr=f"UETR-{random.randint(1000, 9999)}-4b9a-8c11",
            sender_bic=self.bic,
            receiver_bic=receiver_bic,
            beneficiary_name=beneficiary_name,
            safe_approval_token=safe_approval_token,
            cny_debited=cny_received,
            fx_rate=self.SPOT_ASK_RATE,
            gross_usd_purchased=gross_usd,
            correspondent_fee_usd=self.CORRESPONDENT_FEE_USD,
            net_settled_usd=net_settled_usd,
        )
        self.swift_mt103_log.append(mt103)

        print(
            f"  🏦 [{self.name} ({self.bic}) · 代客购汇与 SWIFT 清算] 核验外管局令牌 [{safe_approval_token}] 有效！\n"
            f"     ├─ 银行间外汇市场平盘: 收入备付金 +¥{cny_received:.2f} CNY (@汇率 {self.SPOT_ASK_RATE:.4f}) ➔ 售出本金 ${gross_usd:.2f} USD\n"
            f"     ├─ 扣除跨境清算/电报费: -${self.CORRESPONDENT_FEE_USD:.2f} USD | 扣减本行驻纽约美元头寸池(Nostro): -${net_settled_usd:.2f} USD\n"
            f"     └─ 📡 发送 [SWIFT MT103 报文 {mt103.uetr}]: {self.bic} ➔ {receiver_bic} ({beneficiary_name}) 实汇: ${net_settled_usd:.2f} USD"
        )
        return mt103


class AlipayCrossBorder:
    """
    【支付宝(中国)网络技术有限公司 —— 境内收银台 + 持牌跨境外汇支付机构】
    职责：
    1. 从中国用户的支付宝余额/境内银行卡扣取人民币 (`¥145.00 CNY`)，存入境内《人民币跨境备付金专户》；
    2. 调取用户的实名认证信息（姓名+身份证号），结合境外收单网关（Pockyt）传回的真实子订单号，
       向国家外汇管理局 (`SAFEForeignExchangeBureau`) 发起【逐笔还原申报】；
    3. 拿到外管局电子准购令牌后，将备付金专户里的 `¥145.00 CNY` 划转给境内清算银行 (`OnshoreClearingBank`) 完成购汇与跨境电汇。
    """

    def __init__(self, safe_bureau: SAFEForeignExchangeBureau, clearing_bank: OnshoreClearingBank):
        self.institution_name = "支付宝(中国)网络技术有限公司"
        self.safe_bureau = safe_bureau
        self.clearing_bank = clearing_bank
        self.cny_custody_pool: float = 0.0  # 境内人民币跨境备付金专户余额

    def settle_cross_border_order(
        self,
        buyer_name: str,
        buyer_id_card: str,
        sub_order_id: str,
        overseas_merchant: str,
        receiver_bic: str,
        order_usd: float,
    ) -> SwiftMT103Message:
        # 1. 按合作清算行实时牌价折算人民币并扣款入备付金池
        cny_paid = round(order_usd * self.clearing_bank.SPOT_ASK_RATE, 2)
        self.cny_custody_pool = round(self.cny_custody_pool + cny_paid, 2)
        print(
            f"\n[链路第 1 步：支付宝“惠出境”人民币收银]\n"
            f"  ① [{self.institution_name}] 用户 [{buyer_name}] 选购美区 ${order_usd:.2f} 礼品卡 (子订单:{sub_order_id})，\n"
            f"     支付宝按牌价 {self.clearing_bank.SPOT_ASK_RATE:.4f} 扣取人民币 ¥{cny_paid:.2f} CNY 存入《境内跨境人民币备付金专户》"
        )

        # 2. 向国家外汇管理局(SAFE)提交《逐笔还原申报单》
        print(f"\n[链路第 2 步：SAFE 外管局逐笔还原申报 + 境内清算行代客购汇 + SWIFT MT103 跨境电汇]")
        decl = SAFEDeclaration(
            declaration_no=f"SAFE-DECL-{random.randint(10000, 99999)}",
            payment_institution=self.institution_name,
            buyer_name=buyer_name,
            buyer_id_card=buyer_id_card,
            sub_order_id=sub_order_id,
            overseas_merchant=overseas_merchant,
            bop_code="122030",
            cny_amount=cny_paid,
            fx_usd_amount=order_usd,
        )
        safe_token = self.safe_bureau.submit_restoration_declaration(decl)

        # 3. 将备付金 ¥145.00 CNY 划给境内清算银行，凭外管局令牌购汇并发送 SWIFT MT103 电汇给美国 Pockyt
        self.cny_custody_pool = round(self.cny_custody_pool - cny_paid, 2)
        return self.clearing_bank.execute_spot_fx_and_swift_wire(
            cny_received=cny_paid,
            target_usd=order_usd,
            safe_approval_token=safe_token,
            receiver_bic=receiver_bic,
            beneficiary_name=overseas_merchant,
        )


class InCommDistributor:
    """
    【美国一级礼品卡总代 InComm Payments (亚特兰大) —— POSA 实时点卡激活网关】
    为什么 Pockyt 不能直接找苹果公司买一张 $20 的卡？
    因为苹果总公司 (Apple Inc.) 不对中小电商直接零售卡密，而是授权全球两大一级总代（InComm 与 Blackhawk Network）
    直连苹果内网的【POSA (Point-of-Sale Activation) 激活主机】：
    - 苹果给一级总代 InComm 的批发价：面值的 97.0% ($19.40 USD，即 3% 批发折让)；
    - 一级总代 InComm 给二级分销商 Pockyt 的拿货价：面值的 98.0% ($19.60 USD)；
    - InComm 净赚 $0.20 USD (1.0%) B2B 通道差价，将 $19.40 USD 批发款打给美国苹果总公司，实时激活 16 位兑换码！
    """

    APPLE_WHOLESALE_RATIO = 0.97   # 一级总代向 Apple 拿货价: 97% ($19.40)
    RETAILER_B2B_RATIO = 0.98      # 二级分销商(Pockyt)向 InComm 拿货价: 98% ($19.60)

    def __init__(self, apple_us: "AppleAppStore"):
        self.name = "InComm Payments (美国一级点卡总代)"
        self.apple_us = apple_us
        self.incomm_usd_bank_balance: float = 0.0  # InComm 美国银行账户净留存毛利 (+$0.20)
        self.posa_log: list[InCommPOSARequest] = []

    def activate_via_posa(self, req: InCommPOSARequest) -> AppleGiftCard:
        """收到下游分销商 Pockyt 的 B2B 结算款 ($19.60)，扣除 $0.20 总代毛利后向美国苹果支付 $19.40 激活兑换码"""
        apple_wholesale_usd = round(req.face_value_usd * self.APPLE_WHOLESALE_RATIO, 2)
        incomm_margin_usd = round(req.b2b_payment_usd - apple_wholesale_usd, 2)

        self.incomm_usd_bank_balance = round(self.incomm_usd_bank_balance + incomm_margin_usd, 2)
        self.posa_log.append(req)

        pin_code = f"X9POCKYT{random.randint(10000000, 99999999)}"
        gift_card = self.apple_us.issue_wholesale_gift_card(
            code=pin_code,
            face_value_usd=req.face_value_usd,
            cash_received_usd=apple_wholesale_usd,
        )

        print(
            f"  🔌 [{self.name} · POSA 专线激活] 收到 [{req.retailer_id}] 报文 [{req.request_id}] 与 B2B 货款 ${req.b2b_payment_usd:.2f} USD\n"
            f"     ├─ 一级总代分润清算: InComm 截留总代批零差价 +${incomm_margin_usd:.2f} USD (1.0%)\n"
            f"     ├─ 向苹果总公司付款: 通过美国 ACH 向【Apple Inc. (Cupertino)】支付 97折批发款 ${apple_wholesale_usd:.2f} USD\n"
            f"     └─ ⚡ Apple GSX/POSA 主机实时激活 $20.00 美区礼品卡密: 【{gift_card.code}】"
        )
        return gift_card


class PockytGateway:
    """
    【美国 Pockyt (原 Yuansfer Inc. 源汇，总部纽约) —— 跨境收单网关 & PockytShop 自营商城】
    完整串联境内支付宝与美国本土点卡供应链：
    1. Pockyt 在美国纽约摩根大通银行 (`CHASUS33`) 开有美元收单清算账户 (`jpmorgan_ny_usd_balance`)；
    2. 当中国用户在支付宝“惠出境 -> PockytShop”下单时，Pockyt 调用支付宝跨境接口触发【SAFE 还原申报 + 境内银行购汇电汇】；
    3. 纽约摩根大通账户收到 SWIFT MT103 汇来的 `$19.70 USD` 净额；
    4. Pockyt 自己截留 `$0.10 USD` 商城毛利，向美国一级总代 `InCommDistributor` 支付 `$19.60 USD` 采购款，
       由 InComm 向美国苹果总公司支付 `$19.40 USD` 激活面值 `$20.00` 的美区兑换码！
    """

    RECEIVER_BIC = "CHASUS33 (纽约摩根大通银行 JPMorgan Chase NY)"
    LEGAL_ENTITY = "Yuansfer Inc. DBA Pockyt (New York)"

    def __init__(self, alipay_cb: AlipayCrossBorder, incomm: InCommDistributor):
        self.alipay_cb = alipay_cb
        self.incomm = incomm
        self.jpmorgan_ny_usd_balance: float = 0.0  # Pockyt 纽约摩根大通银行美元账户净留存余额 (+$0.10)

    def buy_from_pockytshop(self, user_name: str, id_card_no: str, face_value_usd: float) -> AppleGiftCard:
        sub_order_id = f"POCKYT-ORD-{random.randint(10000, 99999)}"

        # 第 1~2 步：调用支付宝跨境接口（扣取 ¥145 CNY -> SAFE 逐笔还原申报 -> 中国银行代客购汇 -> SWIFT MT103 电汇至纽约）
        mt103 = self.alipay_cb.settle_cross_border_order(
            buyer_name=user_name,
            buyer_id_card=id_card_no,
            sub_order_id=sub_order_id,
            overseas_merchant=self.LEGAL_ENTITY,
            receiver_bic=self.RECEIVER_BIC,
            order_usd=face_value_usd,
        )

        # 第 3 步：Pockyt 纽约摩根大通账户入账 $19.70 USD，留存 $0.10 毛利，向一级总代 InComm 支付 $19.60 激活礼品卡
        self.jpmorgan_ny_usd_balance = round(self.jpmorgan_ny_usd_balance + mt103.net_settled_usd, 2)
        b2b_cost_to_incomm = round(face_value_usd * InCommDistributor.RETAILER_B2B_RATIO, 2)  # $19.60
        pockyt_margin = round(mt103.net_settled_usd - b2b_cost_to_incomm, 2)                  # $0.10
        self.jpmorgan_ny_usd_balance = round(self.jpmorgan_ny_usd_balance - b2b_cost_to_incomm, 2)

        print(
            f"\n[链路第 3 步：美国 Pockyt (Yuansfer) 纽约入账 ➔ 一级总代 InComm POSA 批发激活]\n"
            f"  🗽 [{self.LEGAL_ENTITY}] 纽约摩根大通账户收到 SWIFT MT103 ({mt103.uetr}) 到账: +${mt103.net_settled_usd:.2f} USD\n"
            f"     ├─ PockytShop 截留自营分销毛利: +${pockyt_margin:.2f} USD (留在纽约摩根大通账户)\n"
            f"     └─ 向美国一级总代 InComm 划付 98折 B2B 采购款: -${b2b_cost_to_incomm:.2f} USD"
        )

        posa_req = InCommPOSARequest(
            request_id=f"POSA-REQ-{random.randint(10000, 99999)}",
            retailer_id="POCKYT-SHOP-US",
            sku_code=f"APPLE-US-GC-{int(face_value_usd)}",
            face_value_usd=face_value_usd,
            b2b_payment_usd=b2b_cost_to_incomm,
        )
        return self.incomm.activate_via_posa(posa_req)


class AppleAppStore:
    """
    【美国苹果总公司 (Apple Inc., Cupertino) —— 法定记录商户 (MoR) 与内部负债核销中心】
    核心金融机制：
    1. 预收款负债池 (deferred_liability_pool)：当礼品卡卖出时，美元真钱已经进了苹果美国银行账户，苹果记下 $20 预收负债。
    2. MoR (Merchant of Record，法定二道贩子)：用户内购订阅 Claude 时，合同双方是【用户 <-> 美国苹果公司】。
       苹果不做任何银行卡网络请求，只做【内部总账负债核销】：
       - 核销用户的 $20.00 Apple ID 负债余额；
       - 确认 30% 苹果税收入 ($6.00)；
       - 在《应付开发者账本 (developer_payable_ledger)》给 Anthropic 记上 +$14.00 USD。
    3. 苹果财月月度结算 (run_monthly_developer_payout)：月底通过美国本土 ACH 银行网将 $14.00 USD 电汇给 Anthropic！
    """

    APPLE_TAX_RATE = 0.30  # 苹果内购抽成 30%

    def __init__(self):
        self.apple_us_bank_cash: float = 0.0              # 美国苹果总公司银行账户里的真实美元现金
        self.unredeemed_card_liability: float = 0.0       # 尚未绑定到 Apple ID 的在外礼品卡负债
        self.apple_id_balances: dict[str, float] = {}     # 美区 Apple ID 账户余额（已绑定的预收款负债）
        self.developer_payables: dict[str, float] = {}    # 应付开发者账款 (如欠 Anthropic 的月度结算款)
        self.net_commission_revenue: float = 0.0          # 苹果净赚的平台佣金（扣除礼品卡批发折让成本）

    def issue_wholesale_gift_card(
        self, code: str, face_value_usd: float, cash_received_usd: float
    ) -> AppleGiftCard:
        """美国苹果收到分销商打来的美元批发款，激活一张面值 $20 的礼品卡，记入预收款负债"""
        self.apple_us_bank_cash = round(self.apple_us_bank_cash + cash_received_usd, 2)
        self.unredeemed_card_liability = round(self.unredeemed_card_liability + face_value_usd, 2)
        return AppleGiftCard(code=code, face_value=face_value_usd, region="US")

    def register_us_apple_id(self, apple_id: str) -> None:
        self.apple_id_balances[apple_id] = 0.0
        print(f"\n[Apple App Store (美区)] 用户已登录美区 Apple ID: [{apple_id}]")

    def redeem_gift_card(self, apple_id: str, gift_card: AppleGiftCard) -> None:
        """用户在美区 App Store 输入兑换码：苹果将负债从“不记名卡池”转移到“用户 Apple ID 账下”"""
        gift_card.redeemed = True
        self.unredeemed_card_liability = round(self.unredeemed_card_liability - gift_card.face_value, 2)
        self.apple_id_balances[apple_id] = round(
            self.apple_id_balances.get(apple_id, 0.0) + gift_card.face_value, 2
        )
        print(
            f"\n[链路第 4 步：美区 Apple ID 充卡 (负债转移)]\n"
            f"  ④ [Apple 内部记账] 兑换码 [{gift_card.code}] 已核验！美国苹果公司将 ${gift_card.face_value:.2f} 预收负债"
            f"绑定至美区账号 [{apple_id}] (当前 Apple 账户余额: ${self.apple_id_balances[apple_id]:.2f} USD)"
        )

    def bind_payment_card(self, apple_id: str, card: VirtualCard, billing_zip: str = "97201") -> bool:
        """
        【模拟 account.apple.com -> 付款与配送 -> 添加付款方式】
        苹果对 Apple ID 绑定银行卡的风控比普通商户消费更严苛，包含三重校验：
        1. 严格锁区校验 (Storefront Country Match)：美区 Apple ID 要求纯正 'US' 本土卡，拒绝 'PR'(波多黎各)/'HK'/'CN' 跨区卡；
        2. 预付虚拟卡 BIN 黑名单：苹果收单行(Chase Paymentech)直接拦截 454924 等高频虚拟 Prepaid 卡作为默认兜底扣款卡；
        3. $0.00 预授权 + AVS (Address Verification System) 账单地址与邮编核验。
        """
        bin_prefix = card.card_number[:6]
        print(
            f"\n[Apple 账户中心 (account.apple.com) · 添加付款方式] "
            f"用户 [{apple_id}] 尝试绑定卡 {bin_prefix}******{card.card_number[-4:]} (账单邮编: {billing_zip})"
        )

        # 校验 1 & 2：Apple 收单风控识别出 454924 是波多黎各(PR)离岸 Prepaid 虚拟卡段
        if card.tier.region != "US" or card.tier.funding_type == "prepaid" or bin_prefix == "454924":
            print(
                f"  🚫 [Apple 风控拦截 · 绑卡失败] 苹果收单系统(Chase Paymentech) 拒绝将此卡保存为默认付款方式！\n"
                f"     ├─ 原因 1 (锁区不匹配): 美区 Apple ID 要求美国本土('US')发行的银行卡，而 BIN [{bin_prefix}] 归属地为 '{card.tier.region}' (波多黎各离岸银行)\n"
                f"     ├─ 原因 2 (拒绝虚拟预付卡兜底): 该卡属性为 '{card.tier.funding_type}' (Prepaid 虚拟卡)，苹果禁止使用高频虚拟卡段 [{bin_prefix}] 作为账户兜底付款方式\n"
                f"     └─ ⚠️ 重要安全警告: 当你账户里已有礼品卡余额 (${self.apple_id_balances.get(apple_id, 0.0):.2f}) 时，"
                f"切勿反复强行绑定 454924 虚拟卡，否则极易触发苹果反欺诈风控导致整个 Apple ID 被禁用！"
            )
            return False

        print(f"  ✅ [Apple 账户中心] $0.00 AVS 地址校验通过，卡片绑定成功！")
        return True

    def subscribe_via_iap(self, apple_id: str, merchant: "Merchant", plan_name: str, price: float) -> bool:
        """
        用户在 iOS 端 App 内点击订阅：
        苹果作为 MoR (Merchant of Record 法定卖方) 进行内部负债核销，并向 Anthropic 发送 JWS 签名凭据
        """
        balance = self.apple_id_balances.get(apple_id, 0.0)
        if balance < price:
            return False

        # 内部会计核销分录：借记 Apple ID 负债 -$20 -> 贷记 应付开发者账款 +$14.00 & 苹果佣金 +$6.00
        self.apple_id_balances[apple_id] = round(balance - price, 2)
        apple_gross_tax = round(price * self.APPLE_TAX_RATE, 2)
        developer_share = round(price - apple_gross_tax, 2)
        self.developer_payables[merchant.name] = round(
            self.developer_payables.get(merchant.name, 0.0) + developer_share, 2
        )

        jws_receipt = f"APPLE-JWS-RECEIPT-{random.randint(10000, 99999)}"
        print(
            f"\n[链路第 5 步：iOS 端内购订阅 —— 苹果作为 MoR (法定记录商户) 执行内部负债核销]\n"
            f"  ⑤ [为什么不用过 Stripe？] 在法律上，是【美国苹果公司】把 Claude 会员卖给用户，苹果自己承担100%反欺诈责任！\n"
            f"     · [Apple 内部会计核销] 扣减 [{apple_id}] 余额 -${price:.2f} (无需调用任何银行/卡组织网络，因为美元真钱早在第③步已进苹果美国银行账户)\n"
            f"     · [Apple 内部切分账本] 苹果截留 30% 苹果税 (${apple_gross_tax:.2f}) | 记入《应付开发者 [{merchant.name}] 月度账本》: +${developer_share:.2f} USD\n"
            f"     · [T+0 实时发货通知] 苹果服务器向 [{merchant.name}] 发送官方加密签名收据: {jws_receipt}"
        )
        merchant.activate_by_apple_iap(apple_id, plan_name, jws_receipt)
        return True

    def run_monthly_developer_payout(
        self,
        merchant: "Merchant",
        clearing_bank: OnshoreClearingBank | None = None,
        pockyt: PockytGateway | None = None,
        incomm: InCommDistributor | None = None,
    ) -> None:
        """【苹果财月月结 T+30~45天】美国苹果总公司通过美国本土 ACH 银行清算网，将 $14.00 USD 真正打入 Anthropic 美国银行卡"""
        payable = self.developer_payables.get(merchant.name, 0.0)
        if payable <= 0:
            return

        self.developer_payables[merchant.name] = 0.0
        self.apple_us_bank_cash = round(self.apple_us_bank_cash - payable, 2)
        merchant.corporate_bank_balance = round(merchant.corporate_bank_balance + payable, 2)

        print(
            f"\n[链路第 6 步：苹果财月月度结算 (T+30天) —— 美元最终流向 Anthropic]\n"
            f"  ⑥ [美国本土 ACH 跨行电汇] 美国苹果总公司 (Apple Inc. US) 从其美国银行账户划出 ${payable:.2f} USD，\n"
            f"     直接打入 [{merchant.name}] 的美国企业对公银行账户！"
        )
        if clearing_bank and pockyt and incomm:
            print(
                f"     📊 【全链路 6 大机构真实账本最终盘点 (¥145.00 CNY ➔ $20.00 USD 分账闭环)】\n"
                f"        ├─ 1. 境内清算银行 ({clearing_bank.bic}): 吸收人民币 +¥{clearing_bank.cny_fx_pool:.2f} CNY | "
                f"跨境电汇手续费收入: +${clearing_bank.fx_fee_revenue_usd:.2f} USD\n"
                f"        ├─ 2. 纽约 Pockyt 网关 (Yuansfer Inc.): 摩根大通美元账户净留存毛利: +${pockyt.jpmorgan_ny_usd_balance:.2f} USD\n"
                f"        ├─ 3. 美国一级总代 (InComm Payments): POSA 分销差价净留存毛利: +${incomm.incomm_usd_bank_balance:.2f} USD\n"
                f"        ├─ 4. 美国苹果总公司 (Apple Inc. US): 银行账户净留存现金: +${self.apple_us_bank_cash:.2f} USD (名义30%苹果税$6.00 - 3%批发折让$0.60)\n"
                f"        └─ 5. 开发者 ({merchant.name}): 美国对公银行账户实收: +${merchant.corporate_bank_balance:.2f} USD "
                f"(合计: ${clearing_bank.fx_fee_revenue_usd + pockyt.jpmorgan_ny_usd_balance + incomm.incomm_usd_bank_balance + self.apple_us_bank_cash + merchant.corporate_bank_balance:.2f} USD)"
            )


class PersonaIdentityGateway:
    """
    【美国第三方实名与生物特征核验平台 Persona (withpersona.com)】
    解答核心迷惑：为什么 Anthropic 封禁中国地区(CN)和香港卡(HK BIN)，但在 Persona 弹窗里选“中国国籍 + 中国护照 + 扫脸”却能 100% 通过？
    因为美国科技公司的合规与反黑产体系严格区分了三个正交维度：
    1. 【地理管辖区围栏 (Geofencing / Location)】：限制物理上位于受限区域境内的网络访问 (client_ip_region)；
    2. 【金融发卡归属地 (BIN Region)】：由 Stripe Radar 拦截未开放金融司法管辖区的银行卡；
    3. 【自然人国籍 (Passport Nationality)】：
       - 受美国《民权法案》(反国籍/族裔歧视法 National Origin Discrimination) 约束，严禁因为一个自然人持有中国护照就拒绝服务
         （全球有数百万持中国护照的硅谷工程师、在美/在日留学生和合法出海开发者）；
       - 只有美国财政部 OFAC 全面金融制裁名单上的极少数国家证件（朝鲜 KP、伊朗 IR、古巴 CU、叙利亚 SY）才会被按国籍一刀切拉黑；
       - Persona 的真正杀手锏是【3D 活体检测 + 护照号哈希 & 面部生物特征向量唯一性去重 (Sybil Attack 防女巫攻击)】：
         黑产工作室能用 BaaS 接口 1 分钟开 1000 张虚拟卡，但黑产老板只有 1 张脸、1 本真护照！
    """

    OFAC_COMPREHENSIVE_SANCTIONS = {"KP", "IR", "CU", "SY"}  # 朝鲜、伊朗、古巴、叙利亚

    def __init__(self, name: str = "Persona 身份核验引擎 (withpersona.com)"):
        self.name = name
        # 护照号单向哈希 -> 已绑定的唯一账号邮箱 (防一本护照认证多个小号)
        self.passport_hash_registry: dict[str, str] = {}
        # 3D 人脸特征向量哈希 -> 已绑定的唯一账号邮箱 (防换证件或同一个人脸认证多个小号)
        self.face_embedding_registry: dict[str, str] = {}

    @staticmethod
    def _hash_feature(raw_value: str) -> str:
        return hashlib.sha256(raw_value.encode()).hexdigest()[:12]

    def verify_inquiry(
        self,
        merchant: "Merchant",
        account_email: str,
        client_ip_region: str,
        passport: PassportDocument,
    ) -> PersonaVerificationResult:
        inquiry_id = f"inq_{random.randint(100000, 999999)}"
        passport_hash = self._hash_feature(f"{passport.nationality}:{passport.passport_no}")
        face_hash = self._hash_feature(passport.face_biometric_id)
        nat_cn = REGION_NAMES.get(passport.nationality, passport.nationality)
        ip_cn = REGION_NAMES.get(client_ip_region, client_ip_region)

        print(
            f"\n  🪪 [{self.name} · 工单 {inquiry_id}] 收到账号 [{account_email}] 的身份核验请求\n"
            f"     ├─ 访问网络位置 (IP Region): {ip_cn} ({client_ip_region}) | 申报证件国籍: {nat_cn} ({passport.nationality})\n"
            f"     ├─ 证件哈希 (Passport Hash): {passport_hash} | 3D人脸特征哈希 (Face Embedding): {face_hash}"
        )

        # 第一关：检查当前访问 IP 是否违反地理围栏 (Geofencing)
        if merchant.blocked_regions and client_ip_region in merchant.blocked_regions:
            reason = f"物理网络位置 ({client_ip_region}) 位于受限司法管辖区 {merchant.blocked_regions} (Geofencing 拦截)"
            print(f"     └─ 🚫 [核验拒绝 · GEOFENCE_IP_BLOCK] {reason}")
            return PersonaVerificationResult(
                inquiry_id=inquiry_id,
                account_email=account_email,
                client_ip_region=client_ip_region,
                passport_nationality=passport.nationality,
                passport_hash=passport_hash,
                face_embedding_hash=face_hash,
                passed=False,
                decision_code="GEOFENCE_IP_BLOCK",
                reason=reason,
            )

        # 第二关：检查是否属于美国财政部 OFAC 全面金融制裁国证件 (如朝鲜 KP、伊朗 IR)
        if passport.nationality in self.OFAC_COMPREHENSIVE_SANCTIONS:
            reason = (
                f"证件签发国 [{nat_cn} ({passport.nationality})] 属于美国财政部 OFAC 全面制裁名单 "
                f"{sorted(self.OFAC_COMPREHENSIVE_SANCTIONS)}，法律强制禁止提供服务"
            )
            print(f"     └─ 🚫 [核验拒绝 · OFAC_SANCTION_BLOCK] {reason}")
            return PersonaVerificationResult(
                inquiry_id=inquiry_id,
                account_email=account_email,
                client_ip_region=client_ip_region,
                passport_nationality=passport.nationality,
                passport_hash=passport_hash,
                face_embedding_hash=face_hash,
                passed=False,
                decision_code="OFAC_SANCTION_BLOCK",
                reason=reason,
            )

        # 第三关：检查摄像头 3D 活体检测 (防 Python 注册脚本 / AI 换脸假视频)
        if not passport.is_live_human:
            reason = "未通过摄像头 3D 活体转头检测 (疑似自动化脚本或静态合成图像)"
            print(f"     └─ 🚫 [核验拒绝 · LIVENESS_FAILED] {reason}")
            return PersonaVerificationResult(
                inquiry_id=inquiry_id,
                account_email=account_email,
                client_ip_region=client_ip_region,
                passport_nationality=passport.nationality,
                passport_hash=passport_hash,
                face_embedding_hash=face_hash,
                passed=False,
                decision_code="LIVENESS_FAILED",
                reason=reason,
            )

        # 第四关（核心杀手锏）：生物特征与护照唯一性去重（防“一人开一万号”的女巫攻击 Sybil Attack）
        existing_acct_by_face = self.face_embedding_registry.get(face_hash)
        existing_acct_by_doc = self.passport_hash_registry.get(passport_hash)
        bound_acct = existing_acct_by_face or existing_acct_by_doc

        if bound_acct and bound_acct != account_email:
            reason = (
                f"命中反女巫攻击去重 (Sybil Defense)！该 3D 人脸特征({face_hash})/护照({passport_hash}) "
                f"已被绑定至另一账号 [{bound_acct}]，严禁一人批量认证多个账号！"
            )
            print(
                f"     └─ 🛑 [核验拒绝 · SYBIL_DUPLICATE_BLOCK] {reason}\n"
                f"        💡 (黑产痛点：号商虽然能用 BaaS 接口 1 分钟开 1000 张虚拟卡，但老板只有 1 张真脸和 1 本真护照！)"
            )
            return PersonaVerificationResult(
                inquiry_id=inquiry_id,
                account_email=account_email,
                client_ip_region=client_ip_region,
                passport_nationality=passport.nationality,
                passport_hash=passport_hash,
                face_embedding_hash=face_hash,
                passed=False,
                decision_code="SYBIL_DUPLICATE_BLOCK",
                reason=reason,
            )

        # 全部通过！记录唯一生物指纹与护照哈希
        self.passport_hash_registry[passport_hash] = account_email
        self.face_embedding_registry[face_hash] = account_email

        compliance_note = (
            f"虽证件国籍为【{nat_cn} ({passport.nationality})】，但：(1) 不在 OFAC 全面制裁名单内；"
            f"(2) 美国《民权法案》禁止基于自然人国籍歧视合法在海外/合规网络下的持照人；"
            f"(3) 已通过 3D 活体与全球唯一生物特征去重，确认为真实唯一人类开发者！"
        )
        print(
            f"     ├─ ✅ [合规与反歧视法校验通过] {compliance_note}\n"
            f"     └─ 🎉 [核验通过 · APPROVED_HUMAN] 已将唯一人脸哈希({face_hash})与护照哈希({passport_hash})永久锁定至账号 [{account_email}]"
        )
        return PersonaVerificationResult(
            inquiry_id=inquiry_id,
            account_email=account_email,
            client_ip_region=client_ip_region,
            passport_nationality=passport.nationality,
            passport_hash=passport_hash,
            face_embedding_hash=face_hash,
            passed=True,
            decision_code="APPROVED_HUMAN",
            reason=compliance_note,
        )


class Merchant:
    """商户"""

    def __init__(
        self,
        name: str,
        acquirer: Acquirer,
        country: str = "US",
        mcc: str = "5734",
        blocked_regions: list[str] | None = None,
        block_prepaid: bool = False,
        blacklisted_bins: list[str] | None = None,
        require_3ds: bool = False,
    ):
        self.name = name
        self.acquirer = acquirer
        self.country = country
        self.mcc = mcc
        self.blocked_regions = blocked_regions
        self.block_prepaid = block_prepaid
        self.blacklisted_bins = blacklisted_bins
        self.require_3ds = require_3ds
        self.corporate_bank_balance: float = 0.0
        self.active_subscribers: list[str] = []
        self.trusted_human_accounts: set[str] = set()
        acquirer.register_merchant(self)

    def checkout(self, card_number: str, cvv: str, expiry: str, amount: float, tx_minute: int = 0) -> bool:
        print(
            f"\n[{self.name} 网页端 Stripe 收银台 ({self.country}·MCC:{self.mcc}) | 时间:T+{tx_minute}m] "
            f"发起扣款: 卡号 {card_number[:6]}******{card_number[-4:]} | 金额 ${amount:.2f}"
        )
        resp = self.acquirer.process_payment(self, card_number, cvv, expiry, amount, tx_minute=tx_minute)
        if resp.approved:
            print(f"[{self.name} 网页端收银台] ✅ 支付成功！")
            return True
        else:
            print(f"[{self.name} 网页端收银台] ❌ 网页弹窗提示：{resp.message}")
            return False

    def activate_by_apple_iap(self, apple_id: str, plan_name: str, jws_receipt: str) -> None:
        """商户后端收到苹果 App Store 的内购成功通知，直接为用户激活订阅"""
        self.active_subscribers.append(apple_id)
        print(
            f"  <- [{self.name} 后端服务器] 校验苹果签名凭据 [{jws_receipt}] 通过！\n"
            f"     🎉 已为账号 [{apple_id}] 成功激活【{plan_name}】权限（可在终端直接登录使用 Claude Code）！"
        )

    def challenge_with_persona(
        self,
        persona_gateway: PersonaIdentityGateway,
        account_email: str,
        client_ip_region: str,
        passport: PassportDocument,
    ) -> bool:
        """
        当网页端检测到中度可疑信号时，Anthropic 弹出 Persona 窗口要求用户拍照上传护照并扫脸：
        验证通过后将账号标记为高信誉真人账号 (trusted_human_accounts)
        """
        print(
            f"\n🛡️ [{self.name} 网页端风控挑战] 账号 [{account_email}] 触发真人身份核验弹窗 ➔ 调用 [{persona_gateway.name}]"
        )
        res = persona_gateway.verify_inquiry(
            merchant=self,
            account_email=account_email,
            client_ip_region=client_ip_region,
            passport=passport,
        )
        if res.passed:
            self.trusted_human_accounts.add(account_email)
            print(f"[{self.name} 风控中心] ✅ 账号 [{account_email}] 已加冕【高信誉唯一真人标签 (Trusted Human)】，解除封号风险！")
            return True
        else:
            print(f"[{self.name} 风控中心] ❌ 账号 [{account_email}] 身份核验未通过 ({res.decision_code})，已限制订阅权限！")
            return False


class User:
    """持卡人"""

    def __init__(self, identifier: str):
        self.identifier = identifier
        self.cards: dict[str, VirtualCard] = {}

    def pay(self, merchant: Merchant, card: VirtualCard, amount: float, tx_minute: int = 0) -> bool:
        print(
            f"\n=== [{self.identifier}] 使用 【{card.tier.display_name}】 在 [{merchant.name}] 刷卡消费 ${amount:.2f} ==="
        )
        return merchant.checkout(card.card_number, card.cvv, card.expiry, amount, tx_minute=tx_minute)

