from bank_card.roles import (
    Acquirer,
    CardNetwork,
    IssuerBank,
    Merchant,
    RoogooPlatform,
    SuPayPlatform,
    User,
)


def main() -> None:
    print("================================================================================")
    print("  阶段 1：刚开出虚拟卡时，VisaNet 的大数据画像库里有什么？（验证信息隔离）")
    print("================================================================================")
    visa_net = CardNetwork("VisaNet")
    us_bank = IssuerBank("美国波多黎各发卡行(Sutton Bank)", region="US", scheme="Visa")
    corp_bank = IssuerBank("美国商业发卡行(Cross River Bank)", region="US", scheme="Mastercard")
    visa_net.register_issuer("428800", us_bank)
    visa_net.register_issuer("556789", corp_bank)

    roogoo = RoogooPlatform("Roogoo 平台")
    roogoo.connect_sponsor_bank(us_bank)
    supay = SuPayPlatform("SuPay 平台")
    supay.connect_sponsor_bank(corp_bank)

    # Alice 在 Roogoo 实名开了一张 $500 额度的虚拟卡；Bob 在 SuPay 匿名开了一张 $100 的卡
    alice = User("Alice")
    roogoo.complete_kyc("Alice")
    roogoo.deposit_usdt("Alice", 500.0)
    alice_card = roogoo.open_card("Alice", "ENJOY", load_amount=500.0)

    bob = User("bob@anon.me")
    supay.deposit_usdt("bob@anon.me", 110.0)
    bob_unused_card = supay.open_card("bob@anon.me", "SUPAY_MC", load_amount=90.0)

    # 重点观察：此时两张卡都已经生成，但从来没刷过，看看 VisaNet 的数据库里有没有它们？
    visa_net.print_card_profiles()

    print("\n================================================================================")
    print("  阶段 2：Alice 正常订阅软件 —— 报文路过 VisaNet，逐步沉淀《卡片历史行为画像》")
    print("================================================================================")
    stripe = Acquirer("Stripe 收单行", visa_net)
    openai = Merchant("OpenAI (美国软件商)", stripe, country="US", mcc="5734")
    cursor = Merchant("Cursor AI (美国软件商)", stripe, country="US", mcc="5734")
    moscow_gift = Merchant("莫斯科数字礼品卡店 (高危洗钱品类)", stripe, country="RU", mcc="5999")

    # 第 1 次刷卡 (T+10 分钟)：Alice 在美国 OpenAI 消费 $20
    alice.pay(openai, alice_card, amount=20.0, tx_minute=10)

    # 第 2 次刷卡 (T+120 分钟，约2小时后)：Alice 在美国 Cursor 消费 $20
    alice.pay(cursor, alice_card, amount=20.0, tx_minute=120)

    # 再次查看 VisaNet 的大数据画像库（对比：Alice 的卡已建立美国常驻画像，而没刷过的 Bob 卡依然隐形）
    visa_net.print_card_profiles()

    print("\n================================================================================")
    print("  阶段 3：黑客盗取 Alice 完整卡号与正确 CVV ➔ 触发 Visa VAA 毫秒级反欺诈熔断")
    print("================================================================================")
    print(
        f"🕵️ [黑客视角] 已通过木马窃取 Alice 卡号 {alice_card.card_number} 与正确 CVV ({alice_card.cvv})，"
        f"此时卡内仍有巨额可用余额 ${alice_card.available_balance:.2f}！"
    )

    # 黑客第 1 步 (T+125 分钟)：先在某美国网站刷一笔 $1.00 小额探卡，测试卡片是否存活
    print("\n--- 🕵️ 黑客第 1 步 (T+125m)：发起 $1.00 小额探卡测试 (Carding Probe) ---")
    openai.checkout(alice_card.card_number, alice_card.cvv, alice_card.expiry, amount=1.0, tx_minute=125)

    # 黑客第 2 步 (T+128 分钟，仅隔 3 分钟！)：确认卡存活后，立即在俄罗斯礼品卡网站盗刷 $300.00！
    print("\n--- 🕵️ 黑客第 2 步 (T+128m，仅隔3分钟)：在俄罗斯商户发起 $300.00 大额礼品卡盗刷 ---")
    moscow_gift.checkout(alice_card.card_number, alice_card.cvv, alice_card.expiry, amount=300.0, tx_minute=128)

    # 最终查看 VisaNet 的画像库与 Alice 的幸存余额
    visa_net.print_card_profiles()
    print(
        f"\n✅ 最终结果：尽管黑客掌握了 100% 正确的卡号、有效期与 CVV，且卡内有 ${alice_card.available_balance:.2f}，"
        f"但凭借 VisaNet 注入报文的 VAA 风险分(99分)，发卡行成功保住了 Alice 的 $300.00！"
    )


if __name__ == "__main__":
    main()
