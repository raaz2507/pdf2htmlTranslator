from __future__ import annotations

from main import IndicTrans2Translator


def main() -> None:
    translator = IndicTrans2Translator()
    checks = [
        ("eng_Latn", "hin_Deva", "Hello world"),
        ("hin_Deva", "eng_Latn", "नमस्ते दुनिया"),
        ("hin_Deva", "mar_Deva", "नमस्ते दुनिया"),
    ]
    for source_language, target_language, text in checks:
        print(f"Caching {source_language} -> {target_language}...")
        translated = translator.translate(text, source_language, target_language)
        print(f"OK: {translated}")


if __name__ == "__main__":
    main()
