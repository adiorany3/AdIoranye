def model_is_free(model: str) -> bool:
    """True hanya untuk model yang benar-benar Rp0/Rp0.

    Jangan hanya mengandalkan kata "free" pada nama model, karena beberapa
    provider memakai suffix free tetapi harga dashboard tetap non-zero.
    """
    model_name = str(model or "").strip()
    lower_name = model_name.lower()

    explicit_price = MODEL_PRICE_IDR.get(model_name)
    if explicit_price is None:
        explicit_price = MODEL_PRICE_IDR.get(lower_name)

    if explicit_price is None:
        for key, value in MODEL_PRICE_IDR.items():
            if str(key).lower() == lower_name:
                explicit_price = value
                break

    if isinstance(explicit_price, dict):
        return (
            int(explicit_price.get("input", 0) or 0) == 0
            and int(explicit_price.get("output", 0) or 0) == 0
        )

    return lower_name.endswith("-free") or lower_name.endswith(":free")
