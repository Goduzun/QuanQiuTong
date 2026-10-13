"""常用战术短语收藏：内置短语库 + 用户自定义收藏，支持快捷插入回复框。"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .storage import atomic_write_json

logger = logging.getLogger("cs2_translator.phrases")


@dataclass
class TacticalPhrase:
    zh: str
    translations: dict = field(default_factory=dict)
    category: str = "战术"
    builtin: bool = False
    phrase_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TacticalPhrase":
        return cls(
            zh=str(data.get("zh", "")),
            translations=dict(data.get("translations") or {}),
            category=str(data.get("category", "战术")),
            builtin=bool(data.get("builtin", False)),
            phrase_id=str(data.get("phrase_id", "")),
        )

    def translation_for(self, language: str) -> str | None:
        if not language:
            return None
        return self.translations.get(language)


# 内置常用战术短语（中文 -> 各目标语言常用写法）。
# 仅作“快捷插入”参考，用户仍可编辑、删除或自行添加。
DEFAULT_PHRASES = [
    TacticalPhrase("冲B点", {"en": "rush B", "ru": "раш Б", "ja": "Bラッシュ", "ko": "B 러시"}, "战术"),
    TacticalPhrase("保枪", {"en": "save", "ru": "сейв", "ja": "セーブ", "ko": "세이브"}, "战术"),
    TacticalPhrase("转点", {"en": "rotate", "ru": "ротейт", "ja": "ローテート", "ko": "로테이트"}, "战术"),
    TacticalPhrase("经济局", {"en": "eco round", "ru": "эко раунд", "ja": "エコ", "ko": "에코 라운드"}, "经济"),
    TacticalPhrase("强起", {"en": "force buy", "ru": "форс-бай", "ja": "フォースバイ", "ko": "포스 바이"}, "经济"),
    TacticalPhrase("A点空了", {"en": "A is clear", "ru": "А чисто", "ja": "Aサイト空いてる", "ko": "A 사이트 비었다"}, "信息"),
    TacticalPhrase("敌人中路", {"en": "enemy mid", "ru": "враг на миде", "ja": "敵ミッド", "ko": "적 미드"}, "信息"),
    TacticalPhrase("小心闪光弹", {"en": "watch the flash", "ru": "осторожно флешка", "ja": "フラッシュ気をつけて", "ko": "플래시 조심해"}, "信息"),
    TacticalPhrase("小心狙击手", {"en": "watch the AWP", "ru": "осторожно снайпер", "ja": "スナイパー気をつけて", "ko": "저격수 조심해"}, "信息"),
    TacticalPhrase("烟雾弹封路了", {"en": "smoked off", "ru": "смок закрыл", "ja": "スモークで塞がれた", "ko": "연막으로 막혔다"}, "信息"),
    TacticalPhrase("跟我走", {"en": "follow me", "ru": "за мной", "ja": "ついてきて", "ko": "따라와"}, "配合"),
    TacticalPhrase("守住这里", {"en": "hold this position", "ru": "держим позицию", "ja": "ここをホールド", "ko": "여기 홀드"}, "配合"),
    TacticalPhrase("救我", {"en": "help!", "ru": "помогите!", "ja": "助けて！", "ko": "도와줘!"}, "配合"),
    TacticalPhrase("给我发把枪", {"en": "drop me a gun", "ru": "дропни оружие", "ja": "武器落として", "ko": "총 좀 버려"}, "配合"),
    TacticalPhrase("干得漂亮", {"en": "nice", "ru": "красава", "ja": "ナイス", "ko": "잘했다"}, "礼貌"),
    TacticalPhrase("好枪", {"en": "nice shot", "ru": "отличный выстрел", "ja": "ナイスショット", "ko": "나이스 샷"}, "礼貌"),
    TacticalPhrase("下包", {"en": "plant the bomb", "ru": "ставь бомбу", "ja": "爆弾を設置", "ko": "설치해"}, "战术"),
    TacticalPhrase("拆包", {"en": "defuse", "ru": "дефьюз", "ja": "爆弾解除", "ko": "해체"}, "战术"),
    TacticalPhrase("补枪", {"en": "trade me", "ru": "трейдни", "ja": "トレードして", "ko": "트레이드"}, "战术"),
    TacticalPhrase("打得好，下把一起", {"en": "gg, queue up next game?", "ru": "gg, следующий матч вместе?", "ja": "gg、次のゲーム一緒にやろう", "ko": "gg, 다음 판 같이 하자"}, "礼貌"),
]


class PhraseBook:
    """短语收藏簿：内置短语 + 用户收藏，持久化在用户数据目录。"""

    def __init__(self, storage_path: Path | None = None):
        self.storage_path = Path(storage_path) if storage_path else None
        self._phrases: list[TacticalPhrase] = []
        self._loaded = False

    # ------------------------------------------------------------------
    def load(self) -> list:
        if self._loaded:
            return list(self._phrases)
        self._phrases = []
        user_phrases = []
        if self.storage_path and self.storage_path.is_file():
            try:
                data = json.loads(self.storage_path.read_text("utf-8"))
                if isinstance(data, list):
                    user_phrases = [TacticalPhrase.from_dict(item) for item in data]
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("短语收藏读取失败: %s", exc)
        # 内置短语（始终从代码刷新，保证新版本内置词条可见）
        builtin_zh = {p.zh for p in DEFAULT_PHRASES}
        for phrase in DEFAULT_PHRASES:
            self._phrases.append(
                TacticalPhrase(
                    zh=phrase.zh,
                    translations=dict(phrase.translations),
                    category=phrase.category,
                    builtin=True,
                    phrase_id=f"builtin:{phrase.zh}",
                )
            )
        for phrase in user_phrases:
            if not phrase.zh or phrase.zh in builtin_zh:
                continue
            if not phrase.phrase_id:
                phrase.phrase_id = f"user:{int(time.time() * 1000)}:{phrase.zh}"
            self._phrases.append(phrase)
        self._loaded = True
        return list(self._phrases)

    def save(self) -> None:
        if not self.storage_path:
            return
        user_phrases = [p for p in self._phrases if not p.builtin]
        atomic_write_json(self.storage_path, [p.to_dict() for p in user_phrases])

    # ------------------------------------------------------------------
    def all(self) -> list:
        return self.load()

    def categories(self) -> list:
        cats = []
        for phrase in self.all():
            if phrase.category not in cats:
                cats.append(phrase.category)
        return cats

    def add(self, zh: str, translations: dict | None = None, category: str = "自定义") -> TacticalPhrase:
        zh = (zh or "").strip()
        if not zh:
            raise ValueError("短语内容不能为空")
        existing = next((p for p in self.all() if p.zh == zh), None)
        if existing is not None:
            if existing.builtin:
                # 内置同名短语：创建用户副本，保留人工译文并可补充新译文
                merged = dict(existing.translations)
                merged.update({k: v for k, v in (translations or {}).items() if v})
                phrase = TacticalPhrase(
                    zh=zh,
                    translations=merged,
                    category=category,
                    builtin=False,
                    phrase_id=f"user:{int(time.time() * 1000)}:{zh}",
                )
                self._phrases.append(phrase)
                self.save()
                return phrase
            if translations:
                existing.translations.update({k: v for k, v in translations.items() if v})
            self.save()
            return existing
        phrase = TacticalPhrase(
            zh=zh,
            translations=dict(translations or {}),
            category=category,
            builtin=False,
            phrase_id=f"user:{int(time.time() * 1000)}:{zh}",
        )
        self._phrases.append(phrase)
        self.save()
        return phrase

    def remove(self, phrase_id: str) -> bool:
        phrases = self.all()
        for index, phrase in enumerate(phrases):
            if phrase.phrase_id == phrase_id:
                if phrase.builtin:
                    return False
                del phrases[index]
                self._phrases = phrases
                self.save()
                return True
        return False

    def get(self, phrase_id: str):
        for phrase in self.all():
            if phrase.phrase_id == phrase_id:
                return phrase
        return None

    @staticmethod
    def translation_for(phrase: TacticalPhrase, language: str) -> str | None:
        return phrase.translation_for(language)
