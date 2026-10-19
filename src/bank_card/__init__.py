from bank_card.roles import (
    Acquirer,
    AlipayGiftCardStore,
    AppleAppStore,
    CardNetwork,
    IssuerBank,
    Merchant,
    RoogooPlatform,
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

    # 初始化 Stripe 收单行（内置 StripeRadar 独立风控引擎）与不同风控等级的商户
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

    # 1. 在 OpenAI 网页刷 4549 2418 55244109 -> 通过 Stripe Radar，到达 Roogoo 扣款成功
    alice.pay(openai, roogoo_card, amount=20.0, tx_minute=10)

    # 2. 在 Anthropic 网页刷 4549 2418 55244109 订阅 Claude Code -> 被 Stripe Radar 第一关直接短路掐断！
    alice.pay(anthropic, roogoo_card, amount=20.0, tx_minute=15)

    # 3. 立即打开 Roogoo 手机 App 核对账单：验证为什么 App 里没有任何失败记录、也没扣 $0.50 拒付费！
    roogoo.print_app_bill(roogoo_card)

    print("\n================================================================================")
    print("  阶段 4：终极破局链路 —— 支付宝买美区 Apple 礼品卡 ➔ 充美区 Apple ID ➔ 内购订阅 Claude")
    print("================================================================================")
    alipay_store = AlipayGiftCardStore()
    us_app_store = AppleAppStore()

    # 第 1 步：在 iPhone 上登录美区 Apple ID
    us_apple_id = "alice_us@icloud.com"
    us_app_store.register_us_apple_id(us_apple_id)

    # 第 2 步：在支付宝小程序里用人民币买一张 $20.00 的美区 Apple Gift Card 兑换码
    gift_card = alipay_store.buy_us_apple_gift_card("Alice", face_value_usd=20.0)

    # 第 3 步：在美区 App Store 兑换礼品卡，余额变为 $20.00
    us_app_store.redeem_gift_card(us_apple_id, gift_card)

    # 第 4 步：在 iOS 端 Claude App 内使用 Apple ID 余额内购订阅 Claude Pro (含 Claude Code)
    us_app_store.subscribe_via_iap(
        apple_id=us_apple_id,
        merchant=anthropic,
        plan_name="Claude Pro (含 Claude Code 终端订阅权限)",
        price=20.0,
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


if __name__ == "__main__":
    main()
