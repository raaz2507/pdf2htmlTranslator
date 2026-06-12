from __future__ import annotations

import os
import sys
import threading
from functools import lru_cache
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))


def load_env_file() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


app = FastAPI(title="IndicTrans2 Local Translation Service")
TRANSLATE_SEMAPHORE = threading.Semaphore(int(os.getenv("INDICTRANS2_CONCURRENCY", "1")))


class TranslationRequest(BaseModel):
    text: str
    source_language: str
    target_language: str


class TranslationResponse(BaseModel):
    translation: str


LANGUAGE_ALIASES = {
    "en": "eng_Latn",
    "hi": "hin_Deva",
    "hi_modern": "hin_Deva",
    "hi_pure": "hin_Deva",
    "bn": "ben_Beng",
    "gu": "guj_Gujr",
    "kn": "kan_Knda",
    "ml": "mal_Mlym",
    "mr": "mar_Deva",
    "or": "ory_Orya",
    "pa": "pan_Guru",
    "ta": "tam_Taml",
    "te": "tel_Telu",
    "ur": "urd_Arab",
}

MODEL_DEFAULTS = {
    "en_indic": "ai4bharat/indictrans2-en-indic-dist-200M",
    "indic_en": "ai4bharat/indictrans2-indic-en-dist-200M",
    "indic_indic": "ai4bharat/indictrans2-indic-indic-dist-320M",
}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "engine": "indictrans2"}


@app.post("/translate", response_model=TranslationResponse)
def translate(request: TranslationRequest) -> TranslationResponse:
    if not request.text.strip():
        return TranslationResponse(translation="")

    translator = get_translator()
    try:
        with TRANSLATE_SEMAPHORE:
            translated = translator.translate(
                request.text,
                normalize_language(request.source_language),
                normalize_language(request.target_language),
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return TranslationResponse(translation=translated)


def normalize_language(code: str) -> str:
    return LANGUAGE_ALIASES.get(code, code)


@lru_cache(maxsize=1)
def get_translator() -> "IndicTrans2Translator":
    return IndicTrans2Translator()


class IndicTrans2Translator:
    def __init__(self) -> None:
        try:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "IndicTrans2 dependencies are not installed. Install indictrans_service/requirements.txt "
                "and download/cache the model before using this provider."
            ) from exc

        try:
            from IndicTransToolkit.processor import IndicProcessor
            processor = IndicProcessor(inference=True)
        except ImportError:
            processor = BasicIndicProcessor()

        self.torch = torch
        self.tokenizer_class = AutoTokenizer
        self.model_class = AutoModelForSeq2SeqLM
        self.processor = processor
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.local_files_only = os.getenv("INDICTRANS2_LOCAL_FILES_ONLY", "0") == "1"
        self.max_length = int(os.getenv("INDICTRANS2_MAX_LENGTH", "256"))
        self.num_beams = int(os.getenv("INDICTRANS2_NUM_BEAMS", "5"))
        self.models: dict[str, tuple[object, object]] = {}

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        direction = self.get_direction(source_language, target_language)
        tokenizer, model = self.get_model(direction)
        batch = self.processor.preprocess_batch(
            [text],
            src_lang=source_language,
            tgt_lang=target_language,
        )
        inputs = tokenizer(
            batch,
            truncation=True,
            padding="longest",
            return_tensors="pt",
            return_attention_mask=True,
        ).to(self.device)
        with self.torch.no_grad():
            outputs = model.generate(
                **inputs,
                use_cache=False,
                min_length=0,
                max_length=self.max_length,
                num_beams=self.num_beams,
                num_return_sequences=1,
            )
        decoded = tokenizer.batch_decode(
            outputs,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=True,
        )
        return self.processor.postprocess_batch(decoded, lang=target_language)[0].strip()

    def get_direction(self, source_language: str, target_language: str) -> str:
        if source_language == "eng_Latn":
            return "en_indic"
        if target_language == "eng_Latn":
            return "indic_en"
        return "indic_indic"

    def get_model(self, direction: str) -> tuple[object, object]:
        if direction in self.models:
            return self.models[direction]

        model_name = os.getenv(
            f"INDICTRANS2_{direction.upper()}_MODEL",
            os.getenv("INDICTRANS2_MODEL", MODEL_DEFAULTS[direction]),
        )
        token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
        model_kwargs = {
            "trust_remote_code": True,
            "local_files_only": self.local_files_only,
        }
        if token:
            model_kwargs["token"] = token
        try:
            tokenizer = self.tokenizer_class.from_pretrained(model_name, **model_kwargs)
            model = self.model_class.from_pretrained(model_name, **model_kwargs).to(self.device)
        except Exception as exc:
            raise RuntimeError(
                "IndicTrans2 model could not be loaded. If this is the first run, set HF_TOKEN "
                "for a Hugging Face account with model access, or set the INDICTRANS2_*_MODEL "
                "environment variable to a local model directory. After caching, set "
                "INDICTRANS2_LOCAL_FILES_ONLY=1 for offline use."
            ) from exc
        if self.device == "cuda":
            model.half()
        model.eval()
        self.models[direction] = (tokenizer, model)
        return self.models[direction]


class BasicIndicProcessor:
    """Small fallback when IndicTransToolkit cannot be built on Windows."""

    def preprocess_batch(
        self,
        batch: list[str],
        src_lang: str,
        tgt_lang: str | None = None,
        is_target: bool = False,
    ) -> list[str]:
        if is_target:
            return [self.normalize_text(text) for text in batch]
        return [f"{src_lang} {tgt_lang} {self.normalize_text(text)}" for text in batch]

    def postprocess_batch(self, sents: list[str], lang: str = "hin_Deva", **_: object) -> list[str]:
        return [self.normalize_text(sent) for sent in sents]

    def normalize_text(self, text: str) -> str:
        return " ".join(text.replace("\xad", "").split())
