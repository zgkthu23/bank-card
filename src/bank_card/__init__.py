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
    print("  阶段 1：构建全球清算网 —— 接入中国大陆(CN)、香港(HK)、波多黎各(PR)与美国本土(US)银行")
    print("================================================================================")
    visa_net = CardNetwork("VisaNet")

    # 1. 中国大陆(CN) 与 中国香港(HK) 传统商业银行 (发行 Credit 信用卡)
    cmb_cn_bank = IssuerBank("招商银行(CMB)", region="CN", scheme="Visa", default_funding="credit")
    boc_cn_bank = IssuerBank("中国银行(Bank of China)", region="CN", scheme="Visa", default_funding="credit")
    hsbc_hk_bank = IssuerBank("香港汇丰银行(HSBC HK)", region="HK", scheme="Visa", default_funding="credit")
    za_hk_bank = IssuerBank("香港虚拟卡合作行(HK Sponsor)", region="HK", scheme="Visa", default_funding="prepaid")
    visa_net.register_issuer("439225", cmb_cn_bank)
    visa_net.register_issuer("409666", boc_cn_bank)
    visa_net.register_issuer("451420", hsbc_hk_bank)
    visa_net.register_issuer("538720", za_hk_bank)

    # 2. 美属波多黎各离岸银行 (PR · 发行 Prepaid 预付卡，如 Roogoo 的 454924)
    pr_bsji_bank = IssuerBank(
        "波多黎各圣胡安国际银行(BSJI)", region="PR", scheme="Visa", default_funding="prepaid"
    )
    # 3. 美国本土商业银行 (US · 发行 Commercial Credit 企业商务信用卡)
    us_cross_river_bank = IssuerBank(
        "美国本土商业银行(Cross River Bank)", region="US", scheme="Mastercard", default_funding="credit"
    )
    visa_net.register_issuer("454924", pr_bsji_bank)
    visa_net.register_issuer("556789", us_cross_river_bank)

    roogoo = RoogooPlatform("Roogoo 平台")
    roogoo.connect_sponsor_bank(pr_bsji_bank)
    roogoo.connect_sponsor_bank(za_hk_bank)

    supay = SuPayPlatform("SuPay 平台")
    supay.connect_sponsor_bank(us_cross_river_bank)

    # 初始化 Stripe 收单行与 3 类风控严苛程度递增的商户：
    stripe = Acquirer("Stripe 收单行", visa_net)
    # 等级 1：Steam (全球无限制，CN/HK/PR/US 全都支持)
    steam = Merchant("Steam 游戏商城 (全球开放)", stripe, country="US", mcc="5734")
    # 等级 2：OpenAI (拦截 CN/HK/RU，但放行美属波多黎各 PR 与 Prepaid 预付卡)
    openai = Merchant(
        "OpenAI (中等风控)",
        stripe,
        country="US",
        mcc="5734",
        blocked_regions=["CN", "HK", "RU"],
    )
    # 等级 3：Anthropic Claude Code (极严风控！除了禁 CN/HK，还在 Stripe Radar 拉黑了 454924 等高危虚拟卡 BIN 并拦截 Prepaid 预付卡)
    anthropic = Merchant(
        "Anthropic Claude Code (极严风控)",
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
    print("  阶段 3：还原真实场景 —— 为什么 454924 能付 OpenAI，买 Claude Code 却显示 Declined？")
    print("================================================================================")
    roogoo.complete_kyc("Alice")
    roogoo.deposit_usdt("Alice", 600.0)
    roogoo_card = roogoo.open_card(
        "Alice",
        "ENJOY",
        load_amount=500.0,
        custom_card_number="4549 2418 55244109",
    )

    # 1. 用 4549 2418 55244109 去付 OpenAI -> 成功！（因为 OpenAI 未封杀 PR 波多黎各与 454924）
    alice.pay(openai, roogoo_card, amount=20.0, tx_minute=10)

    # 2. 用同一张 4549 2418 55244109 去买 Anthropic Claude Code 订阅 -> 触发 Your card has been declined！
    alice.pay(anthropic, roogoo_card, amount=20.0, tx_minute=15)

    # 3. 对比：如果使用底层为【美国本土(US) + Credit(商务信用卡属性)】的卡段去买 Claude Code -> 通过！
    supay.deposit_usdt("alice@work.com", 100.0)
    us_credit_card = supay.open_card("alice@work.com", "SUPAY_MC", load_amount=50.0)
    alice.pay(anthropic, us_credit_card, amount=20.0, tx_minute=18)

    print("\n================================================================================")
    print("  阶段 4：VisaNet VAA 卡片历史行为画像库与黑客跨国盗刷熔断拦截")
    print("================================================================================")
    alice.pay(openai, roogoo_card, amount=20.0, tx_minute=120)
    visa_net.print_card_profiles()

    print(
        f"\n🕵️ [黑客视角] 窃取了 Alice 的真实卡号 {roogoo_card.card_number} 与正确 CVV ({roogoo_card.cvv})，"
        f"先在 T+125m 发起 $1 小额探卡，随后在 T+128m (仅隔3分钟) 跨国在俄罗斯盗刷 $300："
    )
    openai.checkout(roogoo_card.card_number, roogoo_card.cvv, roogoo_card.expiry, amount=1.0, tx_minute=125)
    moscow_gift.checkout(roogoo_card.card_number, roogoo_card.cvv, roogoo_card.expiry, amount=300.0, tx_minute=128)


if __name__ == "__main__":
    main()
