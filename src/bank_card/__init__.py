from bank_card.roles import (
    Acquirer,
    CardNetwork,
    IssuerBank,
    Merchant,
    RoogooPlatform,
    SuPayPlatform,
    User,
    verify_luhn,
)


def main() -> None:
    print("================================================================================")
    print("  阶段 0：解析真实 Roogoo 乐享卡号 [4549 2418 55244109] 的 BIN 归属与 Luhn 校验")
    print("================================================================================")
    real_pan = "4549241855244109"
    print(f"  📌 真实卡号: {real_pan} (格式化: 4549 2418 5524 4109)")
    print(f"  ├─ 首数字 (MII 行业标识): '{real_pan[0]}' ➔ 代表【Visa 卡组织】(注: 4开头必为Visa，5或2开头才是Mastercard)")
    print(f"  ├─ 6位 BIN: {real_pan[:6]} | 8位扩展 BIN: {real_pan[:8]}")
    print("  ├─ 底层持牌发卡行: 波多黎各圣胡安国际银行 (Banco San Juan Internacional, Inc. / BSJI)")
    print("  ├─ 归属国家/地区: 美属波多黎各 (Puerto Rico, PR/US 美元清算区，持 IFE 国际金融实体牌照)")
    print(f"  └─ 最后1位 Luhn 模10校验位: '{real_pan[-1]}' ➔ 算法校验结果: {verify_luhn(real_pan)}")

    print("\n================================================================================")
    print("  阶段 1：构建全球清算网 —— 接入中国大陆(CN)、中国香港(HK)与美属波多黎各(US/PR)发卡行")
    print("================================================================================")
    visa_net = CardNetwork("VisaNet")

    # 1. 中国大陆(CN) 持牌发行 Visa 卡的商业银行
    cmb_cn_bank = IssuerBank("招商银行(CMB)", region="CN", scheme="Visa")
    boc_cn_bank = IssuerBank("中国银行(Bank of China)", region="CN", scheme="Visa")
    visa_net.register_issuer("439225", cmb_cn_bank)
    visa_net.register_issuer("409666", boc_cn_bank)

    # 2. 中国香港(HK) 持牌发行 Visa 卡的商业银行与虚拟卡合作行
    hsbc_hk_bank = IssuerBank("香港汇丰银行(HSBC HK)", region="HK", scheme="Visa")
    za_hk_bank = IssuerBank("香港众安/发卡行(HK Sponsor Bank)", region="HK", scheme="Visa")
    visa_net.register_issuer("451420", hsbc_hk_bank)
    visa_net.register_issuer("538720", za_hk_bank)

    # 3. 美属波多黎各/美国(US) 虚拟卡平台背后的真实持牌发卡行 (BIN 454924 -> BSJI)
    pr_bsji_bank = IssuerBank(
        "波多黎各圣胡安国际银行(Banco San Juan Internacional)", region="US", scheme="Visa"
    )
    us_cross_river_bank = IssuerBank("美国商业发卡行(Cross River Bank)", region="US", scheme="Mastercard")
    visa_net.register_issuer("454924", pr_bsji_bank)
    visa_net.register_issuer("556789", us_cross_river_bank)

    # 虚拟卡平台接入上游银行
    roogoo = RoogooPlatform("Roogoo 平台")
    roogoo.connect_sponsor_bank(pr_bsji_bank)
    roogoo.connect_sponsor_bank(za_hk_bank)

    supay = SuPayPlatform("SuPay 平台")
    supay.connect_sponsor_bank(us_cross_river_bank)

    # 初始化收单行 Stripe 与不同地区政策的海外商户
    stripe = Acquirer("Stripe 收单行", visa_net)
    steam = Merchant("Steam 游戏商城 (全球开放商户)", stripe, country="US", mcc="5734", blocked_regions=None)
    openai = Merchant(
        "OpenAI (受政策限制的AI商户)",
        stripe,
        country="US",
        mcc="5734",
        blocked_regions=["CN", "HK", "RU"],
    )
    moscow_gift = Merchant("莫斯科数字礼品卡店 (高危洗钱品类)", stripe, country="RU", mcc="5999")

    print("\n================================================================================")
    print("  阶段 2：真实痛点还原 —— 为什么有招行/中行/香港汇丰的 Visa 卡，却付不了 OpenAI？")
    print("================================================================================")
    alice = User("Alice")
    cmb_visa = cmb_cn_bank.issue_direct_card("Alice", initial_balance=1000.0, card_name="招商银行全币种国际Visa卡(CN)")
    boc_visa = boc_cn_bank.issue_direct_card("Alice", initial_balance=1000.0, card_name="中国银行长城卓隽留学Visa卡(CN)")
    hsbc_hk_visa = hsbc_hk_bank.issue_direct_card("Alice", initial_balance=1000.0, card_name="香港汇丰银行Pulse Visa卡(HK)")

    print("\n--- [测试 A] 在无政策限制的国际商户 (Steam) 刷中国大陆与香港的 Visa 卡 ---")
    alice.pay(steam, cmb_visa, amount=30.0, tx_minute=1)
    alice.pay(steam, hsbc_hk_visa, amount=30.0, tx_minute=5)

    print("\n--- [测试 B] 拿同样的中国大陆(招行/中行)与香港(汇丰) Visa 卡去付 OpenAI ---")
    alice.pay(openai, cmb_visa, amount=20.0, tx_minute=6)
    alice.pay(openai, boc_visa, amount=20.0, tx_minute=7)
    alice.pay(openai, hsbc_hk_visa, amount=20.0, tx_minute=8)

    print("\n================================================================================")
    print("  阶段 3：使用真实 Roogoo 乐享卡 [4549 2418 55244109] 借道波多黎各(US/PR)破局")
    print("================================================================================")
    roogoo.complete_kyc("Alice")
    roogoo.deposit_usdt("Alice", 600.0)

    # 先试香港卡段 (538720·HK) -> 依然被拦截
    roogoo_hk_card = roogoo.open_card("Alice", "PREMIER", load_amount=50.0)
    alice.pay(openai, roogoo_hk_card, amount=20.0, tx_minute=9)

    # 开通真实卡号 4549 2418 55244109 (BIN 454924 · 波多黎各圣胡安国际银行 BSJI) -> 顺利通过！
    roogoo_us_card = roogoo.open_card(
        "Alice",
        "ENJOY",
        load_amount=500.0,
        custom_card_number="4549 2418 55244109",
    )
    alice.pay(openai, roogoo_us_card, amount=20.0, tx_minute=10)
    alice.pay(openai, roogoo_us_card, amount=20.0, tx_minute=120)

    print("\n================================================================================")
    print("  阶段 4：VisaNet VAA 卡片历史行为画像库与黑客跨国盗刷熔断拦截")
    print("================================================================================")
    visa_net.print_card_profiles()

    print(
        f"\n🕵️ [黑客视角] 窃取了 Alice 的真实卡号 {roogoo_us_card.card_number} 与正确 CVV ({roogoo_us_card.cvv})，"
        f"先在 T+125m 发起 $1 小额探卡，随后在 T+128m (仅隔3分钟) 跨国在俄罗斯盗刷 $300："
    )
    openai.checkout(roogoo_us_card.card_number, roogoo_us_card.cvv, roogoo_us_card.expiry, amount=1.0, tx_minute=125)
    moscow_gift.checkout(roogoo_us_card.card_number, roogoo_us_card.cvv, roogoo_us_card.expiry, amount=300.0, tx_minute=128)


if __name__ == "__main__":
    main()
