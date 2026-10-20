from bank_card.models import PassportDocument
from bank_card.roles import (
    Acquirer,
    AlipayCrossBorder,
    AppleAppStore,
    CardNetwork,
    InCommDistributor,
    IssuerBank,
    Merchant,
    OnshoreClearingBank,
    PersonaIdentityGateway,
    PockytGateway,
    RoogooPlatform,
    SAFEForeignExchangeBureau,
    User,
    verify_luhn,
)


def main() -> None:
    print("================================================================================")
    print("  阶段 0：解析真实 Roogoo 乐享卡号 [4549 2418 55244109] 的 BIN 归属与隐藏标签")
    print("================================================================================")
    real_pan = "4549241855244109"
    print(f"  📌 真实卡号: {real_pan} (格式化: 4549 2418 5524 4109)")
    print(f"  ├─ 首数字 (MII 行业标识): '{real_pan[0]}' ➔ 代表【Visa 卡组织】")
    print(f"  ├─ 6位 BIN: {real_pan[:6]} | 8位扩展 BIN: {real_pan[:8]}")
    print("  ├─ 底层持牌发卡行: 波多黎各圣胡安国际银行 (Banco San Juan Internacional, Inc. / BSJI)")
    print("  ├─ 归属地代码 (ISO Country): 'PR' (Puerto Rico 美属波多黎各离岸金融区，非美国本土 'US')")
    print("  ├─ 卡片资金属性 (Funding Type): 'prepaid' (预付充值卡，而非 'credit' 授信信用卡)")
    print(f"  └─ 最后1位 Luhn 模10校验位: '{real_pan[-1]}' ➔ 算法校验结果: {verify_luhn(real_pan)}")

    print("\n================================================================================")
    print("  阶段 1：构建全球清算网 —— 接入中国大陆(CN)、香港(HK)与美属波多黎各(PR)发卡行")
    print("================================================================================")
    visa_net = CardNetwork("VisaNet")

    cmb_cn_bank = IssuerBank("招商银行(CMB)", region="CN", scheme="Visa", default_funding="credit")
    hsbc_hk_bank = IssuerBank("香港汇丰银行(HSBC HK)", region="HK", scheme="Visa", default_funding="credit")
    pr_bsji_bank = IssuerBank(
        "波多黎各圣胡安国际银行(BSJI)", region="PR", scheme="Visa", default_funding="prepaid"
    )
    visa_net.register_issuer("439225", cmb_cn_bank)
    visa_net.register_issuer("451420", hsbc_hk_bank)
    visa_net.register_issuer("454924", pr_bsji_bank)

    roogoo = RoogooPlatform("Roogoo 平台")
    roogoo.connect_sponsor_bank(pr_bsji_bank)

    stripe = Acquirer("Stripe 收单行", visa_net)
    steam = Merchant("Steam 游戏商城 (全球开放)", stripe, country="US", mcc="5734")
    openai = Merchant(
        "OpenAI (中等风控)",
        stripe,
        country="US",
        mcc="5734",
        blocked_regions=["CN", "HK", "RU"],
    )
    anthropic = Merchant(
        "Anthropic Claude (极严风控)",
        stripe,
        country="US",
        mcc="5734",
        blocked_regions=["CN", "HK", "RU"],
        block_prepaid=True,
        blacklisted_bins=["454924"],
    )
    moscow_gift = Merchant("莫斯科数字礼品卡店 (高危洗钱品类)", stripe, country="RU", mcc="5999")

    print("\n================================================================================")
    print("  阶段 2：为什么招行(CN)和香港汇丰(HK)的 Visa 卡在 Steam 能刷，在 OpenAI 被拒？")
    print("================================================================================")
    alice = User("Alice")
    cmb_visa = cmb_cn_bank.issue_direct_card("Alice", initial_balance=1000.0, card_name="招商银行全币种Visa卡(CN)")
    hsbc_hk_visa = hsbc_hk_bank.issue_direct_card("Alice", initial_balance=1000.0, card_name="香港汇丰Pulse Visa卡(HK)")

    alice.pay(steam, cmb_visa, amount=30.0, tx_minute=1)
    alice.pay(openai, cmb_visa, amount=20.0, tx_minute=6)
    alice.pay(openai, hsbc_hk_visa, amount=20.0, tx_minute=8)

    print("\n================================================================================")
    print("  阶段 3：还原真实体验 —— Stripe Radar 短路拦截 454924（Roogoo App 零记录、零扣费）")
    print("================================================================================")
    roogoo.complete_kyc("Alice")
    roogoo.deposit_usdt("Alice", 500.0)
    roogoo_card = roogoo.open_card(
        "Alice",
        "ENJOY",
        load_amount=500.0,
        custom_card_number="4549 2418 55244109",
    )

    alice.pay(openai, roogoo_card, amount=20.0, tx_minute=10)
    alice.pay(anthropic, roogoo_card, amount=20.0, tx_minute=15)
    roogoo.print_app_bill(roogoo_card)

    print("\n================================================================================")
    print("  阶段 4：深度拆解 Pockyt (Yuansfer) 6 步跨境链路 —— 6 大独立机构如何把 ¥145 变成美元进入 Anthropic？")
    print("================================================================================")
    # 组装境内监管与跨境购汇机构 + 美国本土点卡总代与苹果总公司
    safe_bureau = SAFEForeignExchangeBureau()
    boc_clearing_bank = OnshoreClearingBank(name="中国银行境内跨境清算中心", bic="BKCHCNBJ")
    alipay_cross_border = AlipayCrossBorder(safe_bureau=safe_bureau, clearing_bank=boc_clearing_bank)

    us_app_store = AppleAppStore()
    incomm_us = InCommDistributor(apple_us=us_app_store)
    pockyt_ny = PockytGateway(alipay_cb=alipay_cross_border, incomm=incomm_us)

    us_apple_id = "alice_us@icloud.com"
    us_app_store.register_us_apple_id(us_apple_id)

    # 第 1~3 步：支付宝收 ¥145 -> SAFE 逐笔还原申报 -> 中国银行代客即期购汇 + SWIFT MT103 电汇纽约 -> Pockyt 调 InComm POSA 激活
    gift_card = pockyt_ny.buy_from_pockytshop(
        user_name="Alice", id_card_no="1101011999********", face_value_usd=20.0
    )

    # 第 4 步：美区 App Store 充卡（苹果内部将预收款负债绑定至美区 Apple ID）
    us_app_store.redeem_gift_card(us_apple_id, gift_card)

    # 插曲实测：用户在 account.apple.com 尝试将 454924 虚拟卡绑定为“付款方式”，触发苹果收单三重校验被拒
    us_app_store.bind_payment_card(us_apple_id, roogoo_card)

    # 第 5 步：无需绑定任何银行卡，直接用 Apple 账户余额在 iOS 端内购订阅（苹果作为 MoR 内部核销负债）
    us_app_store.subscribe_via_iap(
        apple_id=us_apple_id,
        merchant=anthropic,
        plan_name="Claude Pro (含 Claude Code 终端订阅权限)",
        price=20.0,
    )

    # 第 6 步：苹果财月月结（T+30天），美国苹果总公司通过美国 ACH 清算网将 $14.00 USD 真正打给 Anthropic，并盘点 6 大机构账本
    us_app_store.run_monthly_developer_payout(
        merchant=anthropic,
        clearing_bank=boc_clearing_bank,
        pockyt=pockyt_ny,
        incomm=incomm_us,
    )

    print("\n================================================================================")
    print("  阶段 5：VisaNet VAA 卡片历史行为画像库与黑客跨国盗刷熔断拦截")
    print("================================================================================")
    alice.pay(openai, roogoo_card, amount=20.0, tx_minute=120)
    visa_net.print_card_profiles()

    print(
        f"\n🕵️ [黑客视角] 窃取了 Alice 的真实卡号 {roogoo_card.card_number} 与正确 CVV ({roogoo_card.cvv})，"
        f"先在 T+125m 发起 $1 小额探卡，随后在 T+128m (仅隔3分钟) 跨国在俄罗斯盗刷 $300："
    )
    openai.checkout(roogoo_card.card_number, roogoo_card.cvv, roogoo_card.expiry, amount=1.0, tx_minute=125)
    moscow_gift.checkout(roogoo_card.card_number, roogoo_card.cvv, roogoo_card.expiry, amount=300.0, tx_minute=128)

    print("\n================================================================================")
    print("  阶段 6：T+1 日终 BASE II 批量清分、全球多边轧差、VSS-110 报表与纽约 Fedwire 头寸调拨")
    print("================================================================================")
    # 模拟双向跨境交易：一位美国游客持摩根大通 Visa 卡来中国上海旅游，刷了招商银行境内 POS 机 $10.00 USD
    cmb_cn_bank.record_inbound_foreign_card_acquiring(
        foreign_tourist="Bob (美国游客)",
        foreign_bank="JPMorgan Chase US",
        domestic_merchant="上海外滩华尔道夫酒店 (招行境内收单 POS)",
        amount_usd=10.0,
    )

    # 第 1 步：Stripe 日终打包上传 BASE II (TC05) 批量请款清分文件（自动剔除黑客那笔未请款的 $1.00 探卡预授权！）
    stripe.submit_base_ii_clearing_batch()

    # 第 2~4 步：VisaNet 执行全球多边轧差 (Multilateral Netting) -> 下发《VSS-110 清算报表》
    #            -> 发卡行本地流水逐笔对账 (解冻未请款 $1) -> 调动纽约代理行 (Nostro) 美元头寸通过 Fedwire 划给 Visa 中央专户
    #            -> Visa 向 Stripe 拨付收单净额
    visa_net.run_base_ii_multilateral_netting_and_settle(acquirer=stripe)

    # 第 5 步：国内持卡人账单日“人民币自动购汇还款”闭环（解答为什么刷招行/中行信用卡不需要提前存美元）
    cmb_cn_bank.auto_repay_credit_bill_in_cny(cmb_visa, spot_fx_rate=7.2500)

    print("\n================================================================================")
    print("  阶段 7：揭秘 Anthropic × Persona 实名风控 —— 为什么封杀中国银行卡(CN BIN)，却放行【中国护照 + 真人扫脸】？")
    print("================================================================================")
    persona_gateway = PersonaIdentityGateway()

    # 实验 1：真实开发者 Alice 挂美国网络 ("US")，在 Persona 弹窗中如实选“中国国籍(CN)”、拍中国护照并扫脸 ➔ 100% 秒过！
    alice_cn_passport = PassportDocument(
        holder_name="Alice",
        nationality="CN",
        passport_no="E88990011",
        face_biometric_id="FACE-3D-VECTOR-ALICE-001",
        is_live_human=True,
    )
    anthropic.challenge_with_persona(
        persona_gateway=persona_gateway,
        account_email="alice_dev@gmail.com",
        client_ip_region="US",
        passport=alice_cn_passport,
    )

    # 实验 2：灰产号商张老板的噩梦 —— 能用 BaaS 开 1000 张虚拟卡，却栽在 Persona 的“唯一人脸/护照去重”手里 (防女巫攻击)
    studio_boss_passport = PassportDocument(
        holder_name="灰产张老板",
        nationality="CN",
        passport_no="E77665544",
        face_biometric_id="FACE-3D-VECTOR-BOSS-ZHANG",
        is_live_human=True,
    )
    print("\n🕵️ [灰产工作室实测] 张老板用自己的中国护照和真脸，先为第 1 个倒卖小号过 Persona 认证：")
    anthropic.challenge_with_persona(
        persona_gateway=persona_gateway,
        account_email="studio_bot_01@gmail.com",
        client_ip_region="US",
        passport=studio_boss_passport,
    )
    print("\n🕵️ [灰产工作室实测] 1 分钟后，张老板的第 2 个小号也弹出 Persona，他试图再次拿同一张脸/同一本护照去扫脸：")
    anthropic.challenge_with_persona(
        persona_gateway=persona_gateway,
        account_email="studio_bot_02@gmail.com",
        client_ip_region="US",
        passport=studio_boss_passport,
    )

    # 实验 3：对比真正会被 Persona 按“国籍”一刀切拉黑的红线 —— OFAC 全面金融制裁国证件 (如伊朗 IR / 朝鲜 KP)
    sanctioned_passport = PassportDocument(
        holder_name="Mallory",
        nationality="IR",
        passport_no="P99001122",
        face_biometric_id="FACE-3D-VECTOR-MALLORY",
        is_live_human=True,
    )
    anthropic.challenge_with_persona(
        persona_gateway=persona_gateway,
        account_email="mallory_test@gmail.com",
        client_ip_region="US",
        passport=sanctioned_passport,
    )


if __name__ == "__main__":
    main()


