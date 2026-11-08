"""Exact English currency wording without floating-point conversion."""

from decimal import Decimal

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split()


def integer_words(value):
    if value < 20:
        return ONES[value]
    if value < 100:
        return TENS[value // 10] + ("-" + ONES[value % 10] if value % 10 else "")
    if value < 1000:
        return (
            ONES[value // 100]
            + " hundred"
            + (" and " + integer_words(value % 100) if value % 100 else "")
        )
    for scale, label in [
        (10**15, "quadrillion"),
        (10**12, "trillion"),
        (10**9, "billion"),
        (10**6, "million"),
        (1000, "thousand"),
    ]:
        if value >= scale:
            rest = value % scale
            return (
                integer_words(value // scale)
                + " "
                + label
                + (
                    (" and " if rest < 100 else " ") + integer_words(rest)
                    if rest
                    else ""
                )
            )


def amount_in_words(amount, currency):
    major, minor, major_plural, minor_plural = {
        "NGN": ("naira", "kobo", "naira", "kobo"),
        "USD": ("US dollar", "cent", "US dollars", "cents"),
        "GBP": ("pound sterling", "penny", "pounds sterling", "pence"),
    }[currency]
    cents = int(Decimal(amount) * 100)
    whole, fraction = divmod(cents, 100)
    words = integer_words(whole) + " " + (major if whole == 1 else major_plural)
    if fraction:
        words += (
            " and "
            + integer_words(fraction)
            + " "
            + (minor if fraction == 1 else minor_plural)
        )
    return words[0].upper() + words[1:] + " only"
