# -*- coding: utf-8 -*-
"""
记忆模块 · 单文件版（memory_onefile）
======================================
整合全部能力于一个文件，拷贝即用，无外部依赖（语义检索需 requests，可选）：

  python memory_onefile.py init|status|demo|selftest
  python memory_onefile.py zone list|create|delete
  python memory_onefile.py subzone list|create|dissolve
  python memory_onefile.py ingest --subzone <sz> -s 摘要 [-c 原文] [--tags a,b] [--route r]
  python memory_onefile.py ingest-auto --subzone <sz> --segments "A|文本" [--keep-original] [--auto-approve]
  python memory_onefile.py search 关键词 [--json] [--top-k N] [--no-rerank] [--no-semantic]
  python memory_onefile.py mirror-search 关键词 [--include-migrated]
  python memory_onefile.py freeze --unit <u> [--unit <u> ...]
  python memory_onefile.py unseal --unit <u>
  python memory_onefile.py remember "摘要" [--detail 原文] [--tags a,b]
      [--premise 适用前提] [--counter 反面坑] [--score 0~1] [--case-type success|fail|neutral]
      [--pin 重要保护] [--source-ref 溯源绑定]
  python memory_onefile.py capture "摘要" [--detail ..] [--tags ..] [--pin] [--source-ref ..]
  python memory_onefile.py export-dir <目录> | import-dir <目录>
  python memory_onefile.py serve --stdio | --http [--port N]   # JSON-RPC 2.0 开放层（只读+propose）
  python memory_onefile.py audit | audit --html [路径]         # 记忆库审计（断网静态报告）
  python memory_onefile.py latest [N] | remove <unit_id>
  python memory_onefile.py augment --id <u> [--premise ..] [--counter ..] [--score ..] [--case-type ..]
      [--pin|--unpin] [--source-ref ..]
  python memory_onefile.py conflict_bind --idA <u> --idB <u>
  python memory_onefile.py feedback --id <u> --result good|bad|neutral [--note ..]
  python memory_onefile.py export --id <u>
  python memory_onefile.py agent "任务" [--learn true|false] [--top N]
  python memory_onefile.py migrate-check | lifecycle | decay
  python memory_onefile.py proposals [--status pending] [--prune --days N]
  python memory_onefile.py approve <id> [--execute] | reject <id> [-r 原因]

白皮书全规则不变：大区/小区仅元信息、单元为唯一正文载体、三层存储、
插件只产提案、核心接口层统一校验执行、镜像检索只读、解封独立指令、
永久回迁溯源来源层。

数据目录解析顺序（与多文件版完全兼容，同一套 JSON）：
  1) 环境变量 MEMORY_DATA_DIR（测试隔离 / 临时覆盖）
  2) 本文件旁的 memory_data_dir.txt 第一行（指向稳定目录，脚本被复制或移动后
     仍然读写同一个记忆库，避免出现"两个库"；建议该文件写 %LOCALAPPDATA%\\doubao-memory\\data）
  3) 本文件同目录 data/
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterator, List, Optional

# ===========================================================================
# 一、配置
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR_POINTER_FILE = "memory_data_dir.txt"


def _resolve_data_dir() -> Path:
    """数据目录解析：环境变量 → 指针文件 → 脚本同目录 data/。

    指针文件（memory_data_dir.txt）第一行即数据目录，用于把脚本与数据解耦：
    无论从桌面副本还是聊天目录副本运行，只要指针指向同一目录，读写的就是同一个
    记忆库，不会因为"拷了一份脚本"而悄悄分裂成两个库。
    """
    env = os.environ.get("MEMORY_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    pointer = PROJECT_ROOT / DATA_DIR_POINTER_FILE
    if pointer.exists():
        try:
            for line in pointer.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return Path(line).expanduser()
        except OSError:
            pass
    return PROJECT_ROOT / "data"


DATA_DIR = _resolve_data_dir()

MAIN_LIBRARY_DIR = DATA_DIR / "main_library"
ZONES_FILE = MAIN_LIBRARY_DIR / "zones.json"
SUBZONES_FILE = MAIN_LIBRARY_DIR / "subzones.json"
MAIN_UNITS_DIR = MAIN_LIBRARY_DIR / "units"

FORGET_LAYER1_DIR = DATA_DIR / "forget_layer1"
L1_SNAPSHOTS_DIR = FORGET_LAYER1_DIR / "snapshots"
L1_RESIDENT_UNITS_DIR = FORGET_LAYER1_DIR / "resident_units"

FORGET_LAYER2_DIR = DATA_DIR / "forget_layer2"
L2_MIRROR_SUBZONES_FILE = FORGET_LAYER2_DIR / "mirror_subzones.json"
L2_COLD_UNITS_DIR = FORGET_LAYER2_DIR / "cold_units"

PROPOSALS_DIR = DATA_DIR / "proposals"
FEEDBACKS_FILE = DATA_DIR / "feedbacks.json"

# 生命周期阈值
DEFAULT_MIGRATION_THRESHOLD = 3
ACTIVE_BASE_SCORE = 10.0
ACTIVE_DECAY_FACTOR = 0.90

# housekeeping 自动巡检：低活跃 + 长期未访问 → 建议冷封存（跳过 pinned 重要经验）
HOUSEKEEP_FREEZE_SCORE = 3.0
HOUSEKEEP_FREEZE_IDLE_DAYS = 30
HOUSEKEEP_LOG_FILE = "housekeeping.log"

# 检索参数
SEARCH_TOP_K = 5
MAX_TOP_K = 50
KEYWORD_MIN_SCORE = 0.05        # 只作用在"相关度 rel"上，不再作用在重排后的综合分
INTROSPECTION_GAP_THRESHOLD = 0.10   # 同样只比较 rel
STALE_DAYS = 30                 # 超过该天数提示"可能过时"
TOUCH_PERSIST_MIN_SECONDS = 3600    # 活跃度落盘节流：1 小时内重复检索不重复写文件

# 语义检索（可插拔，未配置自动降级为关键词）
SEMANTIC_API_KEY = os.environ.get("MEMORY_SEMANTIC_API_KEY", "")
SEMANTIC_BASE_URL = os.environ.get("MEMORY_SEMANTIC_BASE_URL", "")
SEMANTIC_MODEL = os.environ.get("MEMORY_SEMANTIC_MODEL", "text-embedding-3-small")
SEMANTIC_TIMEOUT = 15
SEMANTIC_WEIGHT = 0.6
# 余弦相似度的"无关基线"：embedding 对任何两段中文都容易给出 0.4~0.5 的相似度，
# 直接当相关度用会让无关查询也过阈值。低于该基线的一律折算为 0（按模型可调）。
SEMANTIC_FLOOR = float(os.environ.get("MEMORY_SEMANTIC_FLOOR", "0.45"))

# LLM 接入层（agent 命令用，openai 兼容；未配置时 agent 退化为输出拼装好的两段式提示词）
LLM_API_KEY = os.environ.get("MEMORY_LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("MEMORY_LLM_BASE_URL", "")
LLM_MODEL = os.environ.get("MEMORY_LLM_MODEL", "gpt-4o-mini")
DEFAULT_SCORE = 0.6  # 新经验默认可信度（中性偏信任）

STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "也", "就", "都", "而", "及", "或",
    "这", "那", "我", "你", "他", "她", "它", "们", "一个", "这个", "那个",
    "什么", "怎么", "如何", "请问", "一下", "我们", "你们", "他们", "对于",
    "关于", "进行", "以及", "还有", "没有", "不是", "就是", "可以", "可能",
}


def ensure_dirs() -> None:
    for d in (
        MAIN_LIBRARY_DIR, MAIN_UNITS_DIR,
        FORGET_LAYER1_DIR, L1_SNAPSHOTS_DIR, L1_RESIDENT_UNITS_DIR,
        FORGET_LAYER2_DIR, L2_COLD_UNITS_DIR,
        PROPOSALS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _parse_ts(value) -> Optional[datetime.datetime]:
    """把时间戳解析成带时区的 UTC；历史数据里没有时区的按 UTC 处理；失败返回 None。

    早期版本/手工编辑过的数据可能写成 naive 时间（如 2026-01-01T00:00:00），
    直接和 aware 的 now 相减会抛 TypeError 并让整轮 housekeeping 崩掉。
    """
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _elapsed_days(value, now: Optional[datetime.datetime] = None) -> Optional[float]:
    dt = _parse_ts(value)
    if dt is None:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400.0)


# ===========================================================================
# 二、状态常量
# ===========================================================================
class UnitStatus:
    ACTIVE = "active"
    FROZEN = "frozen"
    MIGRATED = "migrated"
    PERMANENT_MIGRATED = "permanent_migrated"


class ColdMark:
    NONE = "none"
    MIGRATED = "migrated"
    PERMANENT = "permanent_migrated"


class SubzoneStatus:
    ACTIVE = "active"
    DISSOLVED = "dissolved"


class CaseType:
    SUCCESS = "success"   # 成功经验（经实践验证有效）
    FAIL = "fail"         # 踩坑教训（反面案例，专用于提醒风险）
    NEUTRAL = "neutral"   # 中性记录（未验证/通用做法）


class Maturity:
    """经验成熟度状态机（v4）：转换全部由人工 feedback 驱动，AI 不自动改记忆。
    draft（新入库未验证）→ active（首次有效采用）→ trusted（多次验证可信）
    任何状态下 feedback bad → flipped（已翻车，作风险提醒保留，不删除）。
    """
    DRAFT = "draft"       # 待验证：新入库，尚无采用/反馈记录
    ACTIVE = "active"     # 已验证：至少一次 effective 采用
    TRUSTED = "trusted"   # 高可信：多次验证有效
    FLIPPED = "flipped"   # 已翻车：被反馈为 bad，检索时作风险提醒


_MATURITY_LABELS = {
    Maturity.DRAFT: "待验证", Maturity.ACTIVE: "已验证",
    Maturity.TRUSTED: "高可信", Maturity.FLIPPED: "已翻车",
}


def _maturity_label(m: str) -> str:
    return _MATURITY_LABELS.get(m, m)


class ProposalKind:
    INGEST = "ingest"
    DISSOLVE_SUBZONE = "dissolve_subzone"
    MIRROR_SEARCH = "mirror_search"
    PERMANENT_MIGRATE = "permanent_migrate"
    FREEZE = "freeze"          # housekeeping 自动巡检生成的冷封存提案（人工审批后执行）


class ProposalStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"


# ===========================================================================
# 三、数据模型
# ===========================================================================
def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


@dataclass
class Zone:
    zone_id: str
    name: str
    global_tags: list = field(default_factory=list)
    note: str = ""

    @staticmethod
    def create(name: str, global_tags: Optional[list] = None, note: str = "") -> "Zone":
        return Zone(_new_id("z"), name, list(global_tags or []), note)

    def to_dict(self) -> dict:
        return _clean(asdict(self))

    @staticmethod
    def from_dict(d: dict) -> "Zone":
        return Zone(d["zone_id"], d.get("name", ""), list(d.get("global_tags", [])), d.get("note", ""))


@dataclass
class Subzone:
    subzone_id: str
    name: str
    zone_ids: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    routes: list = field(default_factory=list)
    active_route: Optional[str] = None
    note: str = ""
    status: str = SubzoneStatus.ACTIVE

    @staticmethod
    def create(name: str, zone_ids: Optional[list] = None, tags: Optional[list] = None,
               routes: Optional[list] = None, active_route: Optional[str] = None,
               note: str = "") -> "Subzone":
        routes = list(routes or [])
        if active_route and active_route not in routes:
            routes.append(active_route)
        return Subzone(_new_id("sz"), name, list(zone_ids or []), list(tags or []),
                       routes, active_route, note)

    def to_dict(self) -> dict:
        return _clean(asdict(self))

    @staticmethod
    def from_dict(d: dict) -> "Subzone":
        return Subzone(d["subzone_id"], d.get("name", ""), list(d.get("zone_ids", [])),
                       list(d.get("tags", [])), list(d.get("routes", [])),
                       d.get("active_route"), d.get("note", ""),
                       d.get("status", SubzoneStatus.ACTIVE))


@dataclass
class Unit:
    unit_id: str
    subzone_id: str
    summary: str
    content: str = ""
    source_layer: str = "main"
    timestamp: str = ""
    tags: list = field(default_factory=list)
    active_score: float = 0.0
    last_access_at: str = ""
    access_unseal_count: int = 0
    migration_threshold: int = DEFAULT_MIGRATION_THRESHOLD
    version: int = 1
    status: str = UnitStatus.ACTIVE
    route: Optional[str] = None
    # —— 经验增强元字段（v2，旧数据默认空/默认值，完全兼容）——
    premise: str = ""                    # 适用前提：什么时候才能用这条经验
    counter: str = ""                    # 反面/限制/坑：什么情况下不能用
    conflict_ids: list = field(default_factory=list)   # 与哪些经验冲突（双向标记）
    score: float = DEFAULT_SCORE        # 经验可信度 0~1，feedback 会动态调整
    case_type: str = CaseType.NEUTRAL    # success / fail / neutral
    pinned: bool = False                 # 重要经验保护标记：不参与自动冷封存，删除需先解除
    # —— 溯源绑定与成熟度（v4 P0，旧数据默认值兼容）——
    source_ref: str = ""                 # 溯源绑定：经验来自哪个对话/任务/来源（随检索结果返回）
    maturity: str = Maturity.DRAFT       # 成熟度状态机：draft/active/trusted/flipped

    @staticmethod
    def create(subzone_id: str, summary: str, content: str = "",
               tags: Optional[list] = None, source_layer: str = "main",
               route: Optional[str] = None,
               migration_threshold: int = DEFAULT_MIGRATION_THRESHOLD,
               premise: str = "", counter: str = "",
               conflict_ids: Optional[list] = None, score: float = DEFAULT_SCORE,
               case_type: str = CaseType.NEUTRAL, pinned: bool = False,
               source_ref: str = "", maturity: str = Maturity.DRAFT) -> "Unit":
        now = _iso_now()
        return Unit(_new_id("u"), subzone_id, summary, content, source_layer, now,
                    list(tags or []), ACTIVE_BASE_SCORE, now, 0, migration_threshold,
                    1, UnitStatus.ACTIVE, route, premise, counter,
                    list(conflict_ids or []), score, case_type, pinned,
                    source_ref, maturity)

    def to_dict(self) -> dict:
        return _clean(asdict(self))

    @staticmethod
    def from_dict(d: dict) -> "Unit":
        return Unit(d["unit_id"], d["subzone_id"], d.get("summary", ""), d.get("content", ""),
                    d.get("source_layer", "main"), d.get("timestamp", ""),
                    list(d.get("tags", [])), float(d.get("active_score", 0.0)),
                    d.get("last_access_at", ""), int(d.get("access_unseal_count", 0)),
                    int(d.get("migration_threshold", DEFAULT_MIGRATION_THRESHOLD)),
                    int(d.get("version", 1)), d.get("status", UnitStatus.ACTIVE), d.get("route"),
                    d.get("premise", ""), d.get("counter", ""),
                    list(d.get("conflict_ids", [])), float(d.get("score", DEFAULT_SCORE)),
                    d.get("case_type", CaseType.NEUTRAL), bool(d.get("pinned", False)),
                    d.get("source_ref", ""), d.get("maturity", Maturity.DRAFT))

    def touch(self) -> None:
        self.last_access_at = _iso_now()
        self.active_score = max(self.active_score, ACTIVE_BASE_SCORE)


@dataclass
class ColdUnit:
    unit: Unit
    frozen_at: str = ""
    migration_mark: str = ColdMark.NONE

    def to_dict(self) -> dict:
        return _clean({"unit": self.unit.to_dict(),
                       "frozen_at": self.frozen_at or _iso_now(),
                       "migration_mark": self.migration_mark})

    @staticmethod
    def from_dict(d: dict) -> "ColdUnit":
        return ColdUnit(Unit.from_dict(d["unit"]), d.get("frozen_at", ""),
                        d.get("migration_mark", ColdMark.NONE))


@dataclass
class MirrorSubzone:
    subzone_id: str
    name: str
    zone_ids: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    note: str = ""
    mirrored_at: str = ""
    cold_unit_ids: list = field(default_factory=list)

    @staticmethod
    def mirror_from_subzone(sz: Subzone) -> "MirrorSubzone":
        return MirrorSubzone(sz.subzone_id, sz.name, list(sz.zone_ids), list(sz.tags),
                             sz.note, _iso_now())

    def to_dict(self) -> dict:
        return _clean(asdict(self))

    @staticmethod
    def from_dict(d: dict) -> "MirrorSubzone":
        return MirrorSubzone(d["subzone_id"], d.get("name", ""), list(d.get("zone_ids", [])),
                             list(d.get("tags", [])), d.get("note", ""),
                             d.get("mirrored_at", ""), list(d.get("cold_unit_ids", [])))


@dataclass
class Proposal:
    proposal_id: str
    kind: str
    plugin: str
    payload: dict
    created_at: str = ""
    status: str = ProposalStatus.PENDING
    reason: str = ""

    @staticmethod
    def create(kind: str, plugin: str, payload: dict, reason: str = "") -> "Proposal":
        return Proposal(_new_id("p"), kind, plugin, payload, _iso_now(), reason=reason)

    def to_dict(self) -> dict:
        return _clean(asdict(self))

    @staticmethod
    def from_dict(d: dict) -> "Proposal":
        return Proposal(d["proposal_id"], d["kind"], d.get("plugin", ""),
                        d.get("payload", {}), d.get("created_at", ""),
                        d.get("status", ProposalStatus.PENDING), d.get("reason", ""))


@dataclass
class Hit:
    unit: Unit
    score: float          # 排序用综合分（重排关掉时等于 rel）
    source: str = "main"
    rel: float = 0.0      # 纯相关度（关键词/语义混合），阈值与缺口判定只看它


# ===========================================================================
# 四、存储层（JSON 原子写入）
# ===========================================================================
def _read_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_zones() -> List[Zone]:
    return [Zone.from_dict(d) for d in _read_json(ZONES_FILE, [])]


def save_zones(zones: List[Zone]) -> None:
    _write_json(ZONES_FILE, [z.to_dict() for z in zones])


def load_subzones() -> List[Subzone]:
    return [Subzone.from_dict(d) for d in _read_json(SUBZONES_FILE, [])]


def save_subzones(subzones: List[Subzone]) -> None:
    _write_json(SUBZONES_FILE, [s.to_dict() for s in subzones])


def save_unit(unit: Unit) -> None:
    _write_json(MAIN_UNITS_DIR / f"{unit.unit_id}.json", unit.to_dict())


def load_unit(unit_id: str) -> Optional[Unit]:
    p = MAIN_UNITS_DIR / f"{unit_id}.json"
    if not p.exists():
        return None
    return Unit.from_dict(_read_json(p, {}))


def delete_unit(unit_id: str) -> None:
    p = MAIN_UNITS_DIR / f"{unit_id}.json"
    if p.exists():
        p.unlink()


def list_units() -> Iterator[Unit]:
    for p in sorted(MAIN_UNITS_DIR.glob("*.json")):
        try:
            yield Unit.from_dict(_read_json(p, {}))
        except Exception:
            continue


def save_snapshot(unit: Unit, version: int) -> None:
    d = unit.to_dict()
    d["snapshot_version"] = version
    _write_json(L1_SNAPSHOTS_DIR / unit.unit_id / f"{version}.json", d)


def load_snapshots(unit_id: str) -> List[dict]:
    d = L1_SNAPSHOTS_DIR / unit_id
    if not d.exists():
        return []
    return [_read_json(p, {}) for p in sorted(d.glob("*.json"))]


def save_resident_unit(unit: Unit) -> None:
    _write_json(L1_RESIDENT_UNITS_DIR / f"{unit.unit_id}.json", unit.to_dict())


def load_resident_unit(unit_id: str) -> Optional[Unit]:
    p = L1_RESIDENT_UNITS_DIR / f"{unit_id}.json"
    if not p.exists():
        return None
    return Unit.from_dict(_read_json(p, {}))


def list_resident_units() -> Iterator[Unit]:
    for p in sorted(L1_RESIDENT_UNITS_DIR.glob("*.json")):
        try:
            yield Unit.from_dict(_read_json(p, {}))
        except Exception:
            continue


def load_mirror_subzones() -> List[MirrorSubzone]:
    return [MirrorSubzone.from_dict(d) for d in _read_json(L2_MIRROR_SUBZONES_FILE, [])]


def save_mirror_subzones(mirrors: List[MirrorSubzone]) -> None:
    _write_json(L2_MIRROR_SUBZONES_FILE, [m.to_dict() for m in mirrors])


def save_cold_unit(cold: ColdUnit) -> None:
    _write_json(L2_COLD_UNITS_DIR / f"{cold.unit.unit_id}.json", cold.to_dict())


def load_cold_unit(unit_id: str) -> Optional[ColdUnit]:
    p = L2_COLD_UNITS_DIR / f"{unit_id}.json"
    if not p.exists():
        return None
    return ColdUnit.from_dict(_read_json(p, {}))


def list_cold_units() -> Iterator[ColdUnit]:
    for p in sorted(L2_COLD_UNITS_DIR.glob("*.json")):
        try:
            yield ColdUnit.from_dict(_read_json(p, {}))
        except Exception:
            continue


def sync_mirror_cold_ids() -> None:
    mirrors = load_mirror_subzones()
    by_subzone: dict = {}
    for cu in list_cold_units():
        by_subzone.setdefault(cu.unit.subzone_id, []).append(cu.unit.unit_id)
    changed = False
    for m in mirrors:
        new_ids = by_subzone.get(m.subzone_id, [])
        if m.cold_unit_ids != new_ids:
            m.cold_unit_ids = new_ids
            changed = True
    if changed:
        save_mirror_subzones(mirrors)


def save_proposal(p: Proposal) -> None:
    _write_json(PROPOSALS_DIR / f"{p.proposal_id}.json", p.to_dict())


def load_feedbacks() -> list:
    return _read_json(FEEDBACKS_FILE, [])


def append_feedback(unit_id: str, result: str, note: str = "") -> dict:
    rec = {"unit_id": unit_id, "result": result, "note": note, "at": _iso_now()}
    recs = load_feedbacks()
    recs.append(rec)
    _write_json(FEEDBACKS_FILE, recs)
    return rec


def load_proposal(proposal_id: str) -> Optional[Proposal]:
    p = PROPOSALS_DIR / f"{proposal_id}.json"
    if not p.exists():
        return None
    return Proposal.from_dict(_read_json(p, {}))


def list_proposals(status: Optional[str] = None) -> List[Proposal]:
    out = []
    for p in sorted(PROPOSALS_DIR.glob("*.json")):
        try:
            pr = Proposal.from_dict(_read_json(p, {}))
        except Exception:
            continue
        if status is None or pr.status == status:
            out.append(pr)
    return out


def prune_proposals(days: int = 30) -> int:
    """清理已结束（executed/rejected）且文件超过 N 天没动过的提案，避免待批/历史无限膨胀。
    只删提案文件，不触碰任何记忆单元。"""
    days = max(1, int(days))
    cutoff = datetime.datetime.now(datetime.timezone.utc).timestamp() - days * 86400
    removed = 0
    for p in sorted(PROPOSALS_DIR.glob("*.json")):
        try:
            if p.stat().st_mtime > cutoff:
                continue
            pr = Proposal.from_dict(_read_json(p, {}))
            if pr.status not in (ProposalStatus.EXECUTED, ProposalStatus.REJECTED):
                continue
            p.unlink()
            removed += 1
        except Exception:
            continue
    return removed


def pending_proposal_exists(kind: str, predicate) -> bool:
    """是否已有同类待批提案（用于给自动化提案去重，避免每天重复刷同一条）。"""
    for p in list_proposals(status=ProposalStatus.PENDING):
        if p.kind != kind:
            continue
        try:
            if predicate(p):
                return True
        except Exception:
            continue
    return False


# ===========================================================================
# 五、核心接口层（唯一执行数据变更）
# ===========================================================================
class MemoryModuleError(Exception):
    pass


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise MemoryModuleError(msg)


def create_zone(name: str, global_tags: Optional[list] = None, note: str = "") -> Zone:
    _require(name and name.strip(), "大区名称不能为空")
    zone = Zone.create(name.strip(), global_tags, note)
    zones = load_zones()
    zones.append(zone)
    save_zones(zones)
    return zone


def delete_zone(zone_id: str) -> None:
    zones = load_zones()
    _require(any(z.zone_id == zone_id for z in zones), f"大区不存在: {zone_id}")
    save_zones([z for z in zones if z.zone_id != zone_id])
    subzones = load_subzones()
    changed = False
    for s in subzones:
        if zone_id in s.zone_ids:
            s.zone_ids = [i for i in s.zone_ids if i != zone_id]
            changed = True
    if changed:
        save_subzones(subzones)


def create_subzone(name: str, zone_ids: Optional[list] = None, tags: Optional[list] = None,
                   routes: Optional[list] = None, active_route: Optional[str] = None,
                   note: str = "") -> Subzone:
    _require(name and name.strip(), "小区名称不能为空")
    if zone_ids:
        known = {z.zone_id for z in load_zones()}
        missing = [str(i) for i in zone_ids if i not in known]
        _require(not missing, f"要挂载的大区不存在: {', '.join(missing)}")
    sz = Subzone.create(name.strip(), zone_ids, tags, routes, active_route, note)
    subzones = load_subzones()
    subzones.append(sz)
    save_subzones(subzones)
    return sz


def dissolve_subzone(subzone_id: str) -> None:
    """解散小区：仅置状态、保留记录（解封/回迁时可原地复活，归属关系不断裂）。

    硬性拦截覆盖"主库活跃单元 + 遗忘层1 驻留单元"两类在线单元——早期只查主库，
    导致来源层为 layer1 的单元所在小区能被解散，留下永远无法归属的孤儿单元。
    """
    subzones = load_subzones()
    sz = next((x for x in subzones if x.subzone_id == subzone_id), None)
    _require(sz is not None, f"小区不存在: {subzone_id}")
    _require(sz.status == SubzoneStatus.ACTIVE, f"小区已解散: {subzone_id}")
    active_units = [u for u in list_units() if u.subzone_id == subzone_id]
    active_units += [u for u in list_resident_units() if u.subzone_id == subzone_id]
    _require(len(active_units) == 0,
             f"小区仍持有 {len(active_units)} 个活跃单元（含遗忘层1 驻留单元），禁止解散（硬性拦截）")
    sz.status = SubzoneStatus.DISSOLVED
    sz.note = (f"{sz.note} | 已解散").strip(" |")
    save_subzones(subzones)


def get_subzone(subzone_id: str, active_only: bool = False) -> Optional[Subzone]:
    for s in load_subzones():
        if s.subzone_id == subzone_id:
            if active_only and s.status != SubzoneStatus.ACTIVE:
                return None
            return s
    return None


def get_zone(zone_id: str) -> Optional[Zone]:
    return next((z for z in load_zones() if z.zone_id == zone_id), None)


def rebuild_subzone(subzone_id: str, name: str, zone_ids: Optional[list] = None,
                    tags: Optional[list] = None, note: str = "") -> Subzone:
    """按原 ID 重建小区；若记录还在（已解散）则原地复活而不是新建一条。"""
    _require(name and name.strip(), "重建小区名称不能为空")
    subzones = load_subzones()
    existing = next((s for s in subzones if s.subzone_id == subzone_id), None)
    _require(existing is None or existing.status != SubzoneStatus.ACTIVE,
             f"小区已存在且处于活跃状态，无需重建: {subzone_id}")
    if existing is not None:
        existing.name = name.strip()
        existing.zone_ids = list(zone_ids or existing.zone_ids or [])
        existing.tags = list(tags or existing.tags or [])
        existing.note = note or existing.note
        existing.status = SubzoneStatus.ACTIVE
        save_subzones(subzones)
        return existing
    sz = Subzone(subzone_id=subzone_id, name=name.strip(), zone_ids=list(zone_ids or []),
                 tags=list(tags or []), note=note)
    subzones.append(sz)
    save_subzones(subzones)
    return sz


def _ensure_active_subzone(subzone_id: str, reason: str) -> Subzone:
    """解封/回迁前置：目标小区必须存在且活跃；已解散则原地复活，缺失则按镜像重建。"""
    subzones = load_subzones()
    sz = next((s for s in subzones if s.subzone_id == subzone_id), None)
    if sz is not None and sz.status == SubzoneStatus.ACTIVE:
        return sz
    m = _lookup_mirror(subzone_id)
    if sz is not None:                      # 记录还在：原地复活，保留原名称与挂载
        sz.status = SubzoneStatus.ACTIVE
        sz.note = (f"{sz.note} | {reason}").strip(" |")
        if m is not None:
            if not sz.zone_ids:
                sz.zone_ids = list(m.zone_ids)
            if not sz.tags:
                sz.tags = list(m.tags)
        save_subzones(subzones)
        return sz
    return rebuild_subzone(subzone_id, f"[重建]{m.name if m else subzone_id}",
                           list(m.zone_ids) if m else [],
                           list(m.tags) if m else [],
                           f"由{reason}自动重建（原小区已不存在）")


def ingest_unit(subzone_id: str, summary: str, content: str = "",
                tags: Optional[list] = None, source_layer: str = "main",
                route: Optional[str] = None,
                migration_threshold: int = DEFAULT_MIGRATION_THRESHOLD,
                premise: str = "", counter: str = "",
                conflict_ids: Optional[list] = None, score: float = DEFAULT_SCORE,
                case_type: str = CaseType.NEUTRAL, pinned: bool = False,
                source_ref: str = "", maturity: str = Maturity.DRAFT) -> Unit:
    sz = get_subzone(subzone_id, active_only=True)
    _require(sz is not None, f"目标小区不存在或已解散，禁止写入: {subzone_id}")
    _require(summary and summary.strip(), "单元摘要不能为空")
    if route is not None:
        _require(route in sz.routes, f"绑定的 Route 不在小区可用列表: {route}")
    _require(0.0 <= float(score) <= 1.0, f"score 必须在 0~1 之间: {score}")
    _require(case_type in (CaseType.SUCCESS, CaseType.FAIL, CaseType.NEUTRAL),
             f"case_type 只能是 success/fail/neutral: {case_type}")
    _require(maturity in (Maturity.DRAFT, Maturity.ACTIVE, Maturity.TRUSTED, Maturity.FLIPPED),
             f"maturity 只能是 draft/active/trusted/flipped: {maturity}")
    unit = Unit.create(subzone_id, summary.strip(), content, tags, source_layer, route,
                       migration_threshold, premise, counter, conflict_ids, score, case_type,
                       pinned, source_ref, maturity)
    save_unit(unit)
    save_snapshot(unit, version=1)
    return unit


def decay_active_scores(now: Optional[datetime.datetime] = None) -> int:
    """按"距上次访问的天数"从基线重算活跃度，返回真正发生变化的单元数。

    早期实现是 `当前值 * 0.9^距今天数`，同一天跑两次就会把同一个空闲区间乘两遍
    （0.9^d 变成 0.9^(2d)，每天再叠一层，衰减呈指数加速）。这里改成永远从基线算：
    `ACTIVE_BASE_SCORE * 0.9^d`，天然幂等；只降不升，手工调高的热度不会被抹掉。
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    changed = 0
    for unit in list(list_units()):
        days = _elapsed_days(unit.last_access_at, now)
        if days is None:
            continue
        target = max(ACTIVE_BASE_SCORE * (ACTIVE_DECAY_FACTOR ** days), 0.0)
        if unit.active_score > target + 1e-9:
            unit.active_score = round(target, 6)
            save_unit(unit)
            changed += 1
    return changed


def freeze_units(unit_ids: List[str]) -> List[Unit]:
    frozen: List[Unit] = []
    seen: set = set()
    mirrors = {m.subzone_id: m for m in load_mirror_subzones()}
    for uid in unit_ids:
        if not uid or uid in seen:      # 去重必须先于 load_unit，否则重复 --unit 会中途报错
            continue
        seen.add(uid)
        unit = load_unit(uid)
        _require(unit is not None, f"主库单元不存在，无法封存: {uid}")
        sz = get_subzone(unit.subzone_id)
        if sz is not None and unit.subzone_id not in mirrors:
            mirrors[unit.subzone_id] = MirrorSubzone.mirror_from_subzone(sz)
            save_mirror_subzones(list(mirrors.values()))
        cold = ColdUnit(unit=unit, frozen_at=unit.last_access_at or unit.timestamp)
        save_cold_unit(cold)
        delete_unit(uid)
        frozen.append(unit)
    sync_mirror_cold_ids()
    return frozen


def _lookup_mirror(subzone_id: str) -> Optional[MirrorSubzone]:
    return next((m for m in load_mirror_subzones() if m.subzone_id == subzone_id), None)


def unseal_unit(unit_id: str, auto_rebuild: bool = True) -> Unit:
    cold = load_cold_unit(unit_id)
    _require(cold is not None, f"遗忘层2 不存在该冷单元: {unit_id}")
    _require(cold.migration_mark != ColdMark.PERMANENT,
             f"单元已永久迁出，无需再次解封: {unit_id}")
    unit = cold.unit
    target_layer = unit.source_layer
    sz = get_subzone(unit.subzone_id)
    if sz is None or sz.status != SubzoneStatus.ACTIVE:
        if sz is not None and not auto_rebuild:
            raise MemoryModuleError(
                f"原小区已解散({unit.subzone_id})，且未开启自动重建，请先重建小区")
        if sz is None and not auto_rebuild:
            raise MemoryModuleError(
                f"原实体小区已不存在({unit.subzone_id})，且未开启自动重建，请先重建小区")
        _ensure_active_subzone(unit.subzone_id, "解封拉出流程")
    unit.status = UnitStatus.ACTIVE
    unit.touch()
    unit.access_unseal_count += 1
    if target_layer == "layer1":
        save_resident_unit(unit)
    else:
        save_unit(unit)
    cold.migration_mark = ColdMark.MIGRATED
    save_cold_unit(cold)
    sync_mirror_cold_ids()
    return unit


def permanent_migrate(unit_id: str, force: bool = False) -> Unit:
    """永久回迁：把冷单元正式落回来源层，冷库标记 permanent。

    安全性关键点：解封后单元已经在主库，用户可能已经 augment/feedback 改进过它。
    冷库里的那份是"封存时的旧副本"，绝不能用它覆盖主库——否则摘要/前提/可信度/
    版本号会被静默回滚（旧实现正是这么做的）。所以以在线副本为准，冷库只改标记。
    """
    cold = load_cold_unit(unit_id)
    _require(cold is not None, f"遗忘层2 不存在该冷单元: {unit_id}")
    unit = cold.unit
    _require(force or unit.access_unseal_count >= unit.migration_threshold,
             f"解封计数 {unit.access_unseal_count} 未达阈值 {unit.migration_threshold}，禁止永久回迁")
    _ensure_active_subzone(unit.subzone_id, "永久回迁流程")

    source_layer = unit.source_layer
    live = load_resident_unit(unit_id) if source_layer == "layer1" else load_unit(unit_id)
    if live is not None:                       # 在线副本存在 → 以它为准，不回写冷库旧副本
        live.status = UnitStatus.ACTIVE
        live.access_unseal_count = 0
        live.touch()
        if source_layer == "layer1":
            save_resident_unit(live)
        else:
            save_unit(live)
            save_snapshot(live, version=live.version)
        result = live
    else:                                      # 冷库里唯一副本 → 落回来源层
        unit.status = UnitStatus.ACTIVE
        unit.access_unseal_count = 0
        unit.touch()
        unit.version += 1
        if source_layer == "layer1":
            save_resident_unit(unit)
        else:
            save_unit(unit)
            save_snapshot(unit, version=unit.version)
        result = unit
    cold.migration_mark = ColdMark.PERMANENT
    save_cold_unit(cold)
    sync_mirror_cold_ids()
    return result


def approve_proposal(proposal: Proposal) -> None:
    _require(proposal.status == ProposalStatus.PENDING, f"提案非待审状态: {proposal.proposal_id}")
    proposal.status = ProposalStatus.APPROVED
    save_proposal(proposal)


def reject_proposal(proposal: Proposal, reason: str = "人工驳回") -> None:
    _require(proposal.status == ProposalStatus.PENDING, f"提案非待审状态: {proposal.proposal_id}")
    proposal.status = ProposalStatus.REJECTED
    proposal.reason = (proposal.reason + " | " + reason).strip(" |")
    save_proposal(proposal)


_UNIT_PAYLOAD_KEYS = {
    "subzone_id", "summary", "content", "tags", "source_layer", "route",
    "migration_threshold", "premise", "counter", "conflict_ids", "score",
    "case_type", "pinned", "source_ref", "maturity",
}


def _unit_payload(payload: dict) -> dict:
    """提案 payload 白名单校验：外部（serve/import-dir）送来的字段先过滤，
    避免多一个键就把 `ingest_unit(**unit)` 变成 TypeError。"""
    unit = payload.get("unit", {}) if isinstance(payload, dict) else {}
    _require(isinstance(unit, dict), "提案 payload.unit 必须是对象")
    unknown = sorted(set(unit) - _UNIT_PAYLOAD_KEYS)
    _require(not unknown, f"提案 payload 含未知字段（拒绝执行）: {', '.join(unknown)}")
    return unit


def execute_proposal(proposal_id: str, auto_approve: bool = True) -> dict:
    p = load_proposal(proposal_id)
    _require(p is not None, f"提案不存在: {proposal_id}")
    if p.status == ProposalStatus.EXECUTED:
        raise MemoryModuleError(f"提案已执行: {proposal_id}")
    if p.status == ProposalStatus.PENDING and auto_approve:
        approve_proposal(p)
    elif p.status != ProposalStatus.APPROVED:
        raise MemoryModuleError(f"提案未被批准，无法执行: {proposal_id}（当前状态 {p.status}）")
    result: dict = {"proposal_id": p.proposal_id, "kind": p.kind, "plugin": p.plugin}
    if p.kind == ProposalKind.INGEST:
        unit = ingest_unit(**_unit_payload(p.payload))
        result["unit_id"] = unit.unit_id
    elif p.kind == ProposalKind.DISSOLVE_SUBZONE:
        dissolve_subzone(p.payload["subzone_id"])
        result["subzone_id"] = p.payload["subzone_id"]
    elif p.kind == ProposalKind.PERMANENT_MIGRATE:
        unit = permanent_migrate(p.payload["unit_id"])
        result["unit_id"] = unit.unit_id
    elif p.kind == ProposalKind.FREEZE:
        unit_ids = p.payload.get("unit_ids", [])
        frozen = freeze_units(unit_ids)
        result["unit_ids"] = [u.unit_id for u in frozen]
    else:
        raise MemoryModuleError(f"核心接口层不执行该类型提案: {p.kind}（{p.plugin}）")
    p.status = ProposalStatus.EXECUTED
    save_proposal(p)
    return result


# ===========================================================================
# 六、插件（只产提案，无执行权限）
# ===========================================================================
class Plugin:
    name: str = "base"

    def produce(self) -> List[Proposal]:
        return []

    def emit(self, kind: str, payload: dict, reason: str = "") -> Proposal:
        p = Proposal.create(kind=kind, plugin=self.name, payload=payload, reason=reason)
        save_proposal(p)
        return p


HIGH_VALUE_KEYWORDS = [
    "设定", "结论", "参数", "约定", "规则", "剧情", "身份", "目标",
    "决定", "方案", "价格", "配置", "需求", "偏好", "计划", "人物",
    "技能", "权限", "账号", "密码", "地址", "电话", "时间", "截止",
    "记住", "重要", "关键", "禁止", "必须", "默认",
]


class IngestPlugin(Plugin):
    name = "conversation_ingest"

    def scan_conversation(self, segments: List[tuple], default_subzone_id: Optional[str] = None,
                          review_mode: str = "conservative",
                          keep_original: bool = False) -> List[Proposal]:
        """扫描对话片段，产出**本次新建的**提案对象列表（不再返回计数）。

        返回对象让调用方只能执行自己刚生成的提案——早期返回计数、调用方再回头
        扫描"全部 pending"，会把别人的待批草稿一起自动批准，绕过人工审批闸。
        """
        produced: List[Proposal] = []
        _require(default_subzone_id, "对话摄入必须指定目标小区（--subzone）")
        for speaker, text in segments:
            text = (text or "").strip()
            if not self._is_valuable(text):
                continue
            summary = self._summarize(speaker, text)
            tags = self._extract_tags(text)
            conflict, conflict_reason = self._conflict_check(summary, tags)
            reason = f"自动归入[{review_mode}]"
            if conflict:
                reason = f"冲突告警: {conflict_reason}"
            produced.append(self.emit(
                ProposalKind.INGEST,
                {"unit": {"subzone_id": default_subzone_id, "summary": summary,
                          "content": text if keep_original else "", "tags": tags}},
                reason))
        return produced

    @staticmethod
    def _is_valuable(text: str) -> bool:
        if len(text) < 8:
            return False
        lowered = text.lower()
        if any(k in lowered for k in HIGH_VALUE_KEYWORDS):
            return True
        digit_ratio = sum(1 for c in text if c.isdigit() or c in "%$¥￥@#/.") / max(len(text), 1)
        return digit_ratio > 0.12

    @staticmethod
    def _summarize(speaker: str, text: str) -> str:
        first = text.split("。")[0].split(".")[0].split("!")[0].split("！")[0].strip()[:50]
        return f"{speaker}: {first}" if speaker else first

    @staticmethod
    def _extract_tags(text: str) -> List[str]:
        tags = []
        for k in HIGH_VALUE_KEYWORDS:
            if k in text and k not in tags:
                tags.append(k)
        return tags[:5]

    def _conflict_check(self, summary: str, tags: List[str]) -> tuple:
        for unit in list_units():
            overlap = set(tags) & set(unit.tags)
            score = self._similarity(summary, unit.summary)
            cover = self._coverage(summary, unit.summary + " " + unit.content)
            if self._substring_hit(summary, unit.summary):
                return True, f"与 {unit.unit_id} 摘要互相包含，疑似重复归入"
            if overlap and score > 0.35:
                return True, f"与 {unit.unit_id} 标签重叠 {overlap} 且摘要相似 {score:.2f}"
            if score > 0.75:
                return True, f"与 {unit.unit_id} 摘要高度重复({score:.2f})"
            if cover > 0.7:
                return True, f"与 {unit.unit_id} 内容覆盖率达 {cover:.2f}，疑似重复归入"
        return False, ""

    @staticmethod
    def _substring_hit(a: str, b: str) -> bool:
        a, b = a.strip(), b.strip()
        return (len(a) >= 6 and a in b) or (len(b) >= 6 and b in a)

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        def bigrams(s: str):
            return {s[i:i + 2] for i in range(max(len(s) - 1, 0))}
        if not a or not b:
            return 0.0
        ba, bb = bigrams(a), bigrams(b)
        inter = len(ba & bb)
        return inter / len(ba | bb) if (ba | bb) else 0.0

    @staticmethod
    def _coverage(a: str, b: str) -> float:
        def bigrams(s: str):
            return {s[i:i + 2] for i in range(max(len(s) - 1, 0))}
        ba, bb = bigrams(a), bigrams(b)
        if not ba:
            return 0.0
        return len(ba & bb) / len(ba)


class SubzoneLifecyclePlugin(Plugin):
    name = "subzone_lifecycle"

    def produce(self) -> List[Proposal]:
        proposals = []
        active_by_subzone: dict = {}
        for u in list_units():
            active_by_subzone[u.subzone_id] = active_by_subzone.get(u.subzone_id, 0) + 1
        for u in list_resident_units():       # 遗忘层1 驻留单元同样算"持有活跃单元"
            active_by_subzone[u.subzone_id] = active_by_subzone.get(u.subzone_id, 0) + 1
        for sz in load_subzones():
            if sz.status != SubzoneStatus.ACTIVE:      # 已解散的保留记录，不再重复提案
                continue
            if active_by_subzone.get(sz.subzone_id, 0) != 0:
                continue
            if pending_proposal_exists(
                    ProposalKind.DISSOLVE_SUBZONE,
                    lambda p, sid=sz.subzone_id: p.payload.get("subzone_id") == sid):
                continue                                # 已经有待批解散提案，不重复刷
            proposals.append(self.emit(
                ProposalKind.DISSOLVE_SUBZONE, {"subzone_id": sz.subzone_id},
                f"小区「{sz.name}」内活跃单元数为 0（已全部冷封存），触发解散提案"))
        return proposals


class IntrospectionPlugin(Plugin):
    name = "introspection_check"

    def check(self, query: str, hits: List[Hit]) -> List[Proposal]:
        """自省校验：只看**相关度 rel**，不看重排综合分。

        早期用综合分比较 0.10 的缺口阈值，而重排公式含常数底分（≈0.345），
        只要主库非空就永远"信息充足"，遗忘层2 的镜像检索实际再也不会被唤醒。
        """
        def already() -> bool:
            return pending_proposal_exists(
                ProposalKind.MIRROR_SEARCH,
                lambda p: (p.payload.get("query") or "").strip() == query.strip())

        if not hits:
            if already():
                return []
            return [self.emit(ProposalKind.MIRROR_SEARCH, {"query": query},
                              "主库+遗忘层1 检索零召回，判定信息缺口，建议唤醒遗忘层2镜像检索")]
        top = max(h.rel for h in hits)
        if top < INTROSPECTION_GAP_THRESHOLD:
            if already():
                return []
            return [self.emit(ProposalKind.MIRROR_SEARCH, {"query": query},
                              f"热区最高相关度 {top:.3f} < 缺口阈值 {INTROSPECTION_GAP_THRESHOLD}，建议镜像检索")]
        return []


class HotMigrationPlugin(Plugin):
    name = "hot_migration"

    def monitor(self) -> List[Proposal]:
        proposals = []
        for cold in list_cold_units():
            if cold.migration_mark != ColdMark.MIGRATED:
                continue
            unit = cold.unit
            if unit.access_unseal_count < unit.migration_threshold:
                continue
            if pending_proposal_exists(
                    ProposalKind.PERMANENT_MIGRATE,
                    lambda p, uid=unit.unit_id: p.payload.get("unit_id") == uid):
                continue
            proposals.append(self.emit(
                ProposalKind.PERMANENT_MIGRATE, {"unit_id": unit.unit_id},
                f"单元 {unit.unit_id} 解封计数 {unit.access_unseal_count} "
                f"达到阈值 {unit.migration_threshold}，触发永久回迁提案"))
        return proposals


# ===========================================================================
# 七、检索（关键词 + 可插拔语义 + 链路）
# ===========================================================================
_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokenize(text: str) -> set:
    text = (text or "").lower()
    feats: set = set()
    cn = re.sub(r"[^\u4e00-\u9fff]", "", text)
    for i in range(len(cn) - 1):
        feats.add(cn[i:i + 2])
    if len(cn) == 1:
        feats.add(cn)
    for w in _WORD_RE.findall(text):
        feats.add(w)
    for w in STOPWORDS:
        feats.discard(w)
    return feats


def _kw_score(query: str, unit: Unit) -> float:
    qf = _tokenize(query)
    if not qf:
        return 0.0
    tag_text = " ".join(unit.tags).lower()
    tag_feats = _tokenize(tag_text)
    tag = len(qf & tag_feats) / len(qf) if tag_feats else 0.0

    def coverage(text: str) -> float:
        df = _tokenize(text)
        if not df:
            return 0.0
        return len(qf & df) / len(qf)

    cov_summary = coverage(unit.summary)
    cov_content = coverage(unit.content) * 0.5
    return 0.4 * tag + 0.4 * cov_summary + 0.2 * cov_content


_EMBED_CACHE: dict = {}          # 单条文本 → 向量（按 unit 缓存，跨查询复用）
_EMBED_CACHE_LIMIT = 4096
_LOCAL_EMBED_MODEL_OBJ = None    # 本地模型惰性单例（不再每次检索重新加载）


LOCAL_EMBED_ENABLED = os.environ.get("MEMORY_LOCAL_EMBED", "").strip().lower() in ("1", "true", "yes")
_LOCAL_EMBED_MODEL = os.environ.get("MEMORY_LOCAL_EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
_LOCAL_EMBED_CACHE = os.environ.get("MEMORY_EMBED_CACHE") or None


def _semantic_available() -> bool:
    return bool(SEMANTIC_API_KEY and SEMANTIC_BASE_URL) or _local_semantic_available()


def _local_semantic_available() -> bool:
    """本地语义重排（P2 可选依赖）：MEMORY_LOCAL_EMBED=1 且已安装 fastembed 时才启用。
    未安装/异常一律视为不可用，自动降级为关键词/API，零依赖卖点不受影响。"""
    if not LOCAL_EMBED_ENABLED:
        return False
    try:
        import fastembed  # noqa: F401
        return True
    except Exception:
        return False


def _cosine(qv, v) -> float:
    if not qv or not v or len(qv) != len(v):
        return 0.0
    dot = sum(x * y for x, y in zip(qv, v))
    na = math.sqrt(sum(x * x for x in qv))
    nb = math.sqrt(sum(y * y for y in v))
    return dot / (na * nb) if na and nb else 0.0


def _cache_get(key: str):
    return _EMBED_CACHE.get(key)


def _cache_put(key: str, vec) -> None:
    if len(_EMBED_CACHE) >= _EMBED_CACHE_LIMIT:
        for k in list(_EMBED_CACHE)[: _EMBED_CACHE_LIMIT // 4]:
            _EMBED_CACHE.pop(k, None)
    _EMBED_CACHE[key] = vec


def _local_embed(texts: List[str], batch: int = 32) -> Optional[List[list]]:
    """惰性加载并复用同一个 fastembed 模型实例（模型加载是秒级开销，不能每次检索重来）。"""
    global _LOCAL_EMBED_MODEL_OBJ
    try:
        from fastembed import TextEmbedding
        if _LOCAL_EMBED_MODEL_OBJ is None:
            _LOCAL_EMBED_MODEL_OBJ = TextEmbedding(model_name=_LOCAL_EMBED_MODEL,
                                                   cache_dir=_LOCAL_EMBED_CACHE)
        out: List[list] = []
        for i in range(0, len(texts), batch):
            out.extend(list(v) for v in _LOCAL_EMBED_MODEL_OBJ.embed(texts[i:i + batch]))
        return out
    except Exception:
        return None


def _local_semantic_scores(query: str, texts: List[str]) -> Optional[List[float]]:
    """本地向量余弦相似度；向量按文本缓存，命中缓存就不再喂模型。"""
    if not LOCAL_EMBED_ENABLED or not texts:
        return None
    missing = [t for t in [query] + texts if _cache_get(f"local|{_LOCAL_EMBED_MODEL}|{t}") is None]
    if missing:
        vecs = _local_embed(missing)
        if vecs is None or len(vecs) != len(missing):
            return None
        for t, v in zip(missing, vecs):
            _cache_put(f"local|{_LOCAL_EMBED_MODEL}|{t}", v)
    qv = _cache_get(f"local|{_LOCAL_EMBED_MODEL}|{query}")
    return [_cosine(qv, _cache_get(f"local|{_LOCAL_EMBED_MODEL}|{t}")) for t in texts]


def _api_embed(texts: List[str]) -> Optional[List[list]]:
    try:
        import requests
        resp = requests.post(
            f"{SEMANTIC_BASE_URL.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {SEMANTIC_API_KEY}"},
            json={"model": SEMANTIC_MODEL, "input": texts},
            timeout=SEMANTIC_TIMEOUT,
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda x: x["index"])
        return [d["embedding"] for d in data]
    except Exception:
        return None


def _semantic_scores(query: str, texts: List[str]) -> Optional[List[float]]:
    """云端 /embeddings：只对未缓存的文本发起请求（早期按"查询+整个候选池"做键，
    池子一变就必然 miss，等于永远不命中还无限占内存）。"""
    if not _semantic_available() or not texts:
        return None
    prefix = f"api|{SEMANTIC_BASE_URL}|{SEMANTIC_MODEL}|"
    missing: List[str] = []
    for t in [query] + texts:
        if _cache_get(prefix + t) is None and t not in missing:
            missing.append(t)
    if missing:
        vecs = _api_embed(missing)
        if vecs is None or len(vecs) != len(missing):
            return None
        for t, v in zip(missing, vecs):
            _cache_put(prefix + t, v)
    qv = _cache_get(prefix + query)
    return [_cosine(qv, _cache_get(prefix + t)) for t in texts]


def _semantic_backend_scores(query: str, texts: List[str]):
    """按优先级取语义分：本地 fastembed → API；都不可用返回 (None, "")。"""
    if not texts:
        return None, ""
    if LOCAL_EMBED_ENABLED:
        s = _local_semantic_scores(query, texts)
        if s is not None:
            return s, "local"
    if SEMANTIC_API_KEY and SEMANTIC_BASE_URL:
        s = _semantic_scores(query, texts)
        if s is not None:
            return s, "api"
    return None, ""


@dataclass
class SearchResult:
    query: str
    hot_hits: List[Hit]
    gap: bool
    mirror_proposal_id: Optional[str]
    cold_hits: List[Hit]
    semantic_used: bool
    semantic_backend: str = ""


class SearchEngine:
    """检索：先算相关度 → 阈值过滤 → 重排排序 → 取 top_k → 冲突强制打包。"""

    def __init__(self, use_semantic: bool = True):
        self.use_semantic = use_semantic
        self.introspection = IntrospectionPlugin()

    def hot_pool(self) -> List[Unit]:
        pool = list(list_units())
        pool.extend(list_resident_units())
        return pool

    def _hybrid_scores(self, query: str, pool: List[Unit]):
        """返回 [(rel, unit)]。rel 是纯相关度，不掺可信度/热度，
        因此它可以直接和阈值比较——这是"无关查询不召回"的前提。"""
        kw = [_kw_score(query, u) for u in pool]
        sem = None
        sem_backend = ""
        if self.use_semantic:
            sem, sem_backend = _semantic_backend_scores(
                query, [u.summary + " " + u.content for u in pool])
        fused = []
        for i, u in enumerate(pool):
            k = kw[i]
            if sem is None:
                s = k
            else:
                # 余弦先减去无关基线，再和关键词分加权；否则语义会把无关内容也抬过阈值
                sem_rel = max(0.0, (sem[i] - SEMANTIC_FLOOR) / max(1e-6, 1.0 - SEMANTIC_FLOOR))
                s = SEMANTIC_WEIGHT * sem_rel + (1 - SEMANTIC_WEIGHT) * k
            fused.append((s, u))
        return fused, sem is not None, sem_backend

    @staticmethod
    def _storage_of(unit: Unit) -> str:
        return "main" if (MAIN_UNITS_DIR / f"{unit.unit_id}.json").exists() else "layer1"

    @staticmethod
    def _touch(unit: Unit) -> None:
        """刷新活跃度：内存必更，落盘节流（1 小时内重复检索不重复写文件）。
        既保留"主库召回刷新热度"的语义，又避免把检索变成写放大。"""
        prev = _parse_ts(unit.last_access_at)
        unit.touch()
        if prev is not None:
            delta = (datetime.datetime.now(datetime.timezone.utc) - prev).total_seconds()
            if 0 <= delta < TOUCH_PERSIST_MIN_SECONDS:
                return
        if SearchEngine._storage_of(unit) == "main":
            save_unit(unit)
        else:
            save_resident_unit(unit)

    def _rerank_score(self, rel: float, unit: Unit) -> float:
        """综合排序分 = 相关度 0.60 + 可信度 0.22 + 热度 0.18 + 类型微调。
        不再包含常数底分：相关度 0 的单元在过滤阶段就被剔除，因此不会出现
        "无关经验也拿到 0.345 分"的假命中，也不会让缺口判定永远失效。"""
        heat = min(unit.active_score / ACTIVE_BASE_SCORE, 1.0)
        type_bonus = (0.03 if unit.case_type == CaseType.SUCCESS
                      else (-0.03 if unit.case_type == CaseType.FAIL else 0.0))
        return 0.60 * rel + 0.22 * unit.score + 0.18 * heat + type_bonus

    def search(self, query: str, top_k: Optional[int] = None, rerank: bool = True) -> SearchResult:
        top_k = max(1, min(int(top_k or SEARCH_TOP_K), MAX_TOP_K))
        pool = self.hot_pool()
        if not pool:
            result = SearchResult(query, [], True, None, [], False)
            self._auto_mirror_search(result)
            return result
        fused, sem_used, sem_backend = self._hybrid_scores(query, pool)
        # ① 阈值只作用于相关度：无关单元不进候选（与重排解耦）
        fused = [(rel, u) for rel, u in fused if rel >= KEYWORD_MIN_SCORE]
        if not fused:
            result = SearchResult(query, [], True, None, [], sem_used, sem_backend)
            self._auto_mirror_search(result)
            return result
        # ② 排序 → ③ 严格取 top_k（不再放大到 3 倍/2 倍）
        if rerank:
            fused.sort(key=lambda x: self._rerank_score(x[0], x[1]), reverse=True)
        else:
            fused.sort(key=lambda x: x[0], reverse=True)
        hot_hits: List[Hit] = []
        for rel, u in fused[:top_k]:
            score = self._rerank_score(rel, u) if rerank else rel
            self._touch(u)
            hot_hits.append(Hit(u, score, self._storage_of(u), rel))
        # ④ 冲突强制成对：追加在 top_k 之后，且不参与截断
        if rerank and hot_hits:
            hot_hits = self._pack_conflicts(hot_hits, pool)
        result = SearchResult(query, hot_hits, False, None, [], sem_used, sem_backend)
        self._auto_mirror_search(result)
        return result

    @staticmethod
    def _pack_conflicts(hits: List[Hit], pool: List[Unit]) -> List[Hit]:
        """冲突打包：命中单元声明了 conflict_ids 且冲突对象在候选池内 → 强制追加。

        成对返回是硬约束（不静默取舍），所以追加项不受 top_k 截断——宁可多一条，
        也不能让"冲突的另一半"被切掉。
        """
        by_id = {u.unit_id: u for u in pool}
        selected = {h.unit.unit_id for h in hits}
        extra: List[Hit] = []
        for h in hits:
            for cid in h.unit.conflict_ids:
                if cid in selected or any(e.unit.unit_id == cid for e in extra):
                    continue
                cu = by_id.get(cid)
                if cu is None:
                    continue
                extra.append(Hit(cu, max(h.score - 0.15, 0.0), "conflict",
                                 max(h.rel - 0.15, 0.0)))
        return hits + extra

    def _auto_mirror_search(self, result: SearchResult) -> None:
        proposals = self.introspection.check(result.query, result.hot_hits)
        if not proposals:
            return
        result.gap = True
        result.mirror_proposal_id = proposals[0].proposal_id
        result.cold_hits = self.mirror_search(result.query)

    def mirror_search(self, query: str, top_k: Optional[int] = None,
                      include_migrated: bool = False) -> List[Hit]:
        top_k = max(1, min(int(top_k or SEARCH_TOP_K), MAX_TOP_K))
        hits = []
        for cold in list_cold_units():
            if not include_migrated and cold.migration_mark != ColdMark.NONE:
                continue
            s = _kw_score(query, cold.unit)
            if s >= KEYWORD_MIN_SCORE:
                hits.append(Hit(cold.unit, s, "mirror", s))
        hits.sort(key=lambda x: x.score, reverse=True)
        return hits[:top_k]


# ===========================================================================
# 八、极简命令（普通人友好：remember / latest / status / remove）
# ===========================================================================
DEFAULT_ZONE_NAME = "个人经验"
DEFAULT_SUBZONE_NAME = "日常经验库"


def _ensure_default_library() -> Subzone:
    """确保「个人经验 / 日常经验库」可用；默认库被解散过就原地复活，不会二义。"""
    zones = load_zones()
    z = next((x for x in zones if x.name == DEFAULT_ZONE_NAME), None)
    if z is None:
        z = create_zone(DEFAULT_ZONE_NAME, ["经验"], "个人长期经验库（由记忆插件自动维护）")
    subzones = load_subzones()
    sz = next((x for x in subzones
               if x.name == DEFAULT_SUBZONE_NAME and x.status == SubzoneStatus.ACTIVE), None)
    if sz is not None:
        return sz
    old = next((x for x in subzones if x.name == DEFAULT_SUBZONE_NAME), None)
    if old is not None:
        return rebuild_subzone(old.subzone_id, DEFAULT_SUBZONE_NAME, [z.zone_id],
                               ["经验"], "日常经验默认收纳区")
    return create_subzone(DEFAULT_SUBZONE_NAME, [z.zone_id], ["经验"], note="日常经验默认收纳区")


def _age_days(iso_ts: str) -> int:
    days = _elapsed_days(iso_ts)
    return 0 if days is None else int(days)


def _fmt_date(iso_ts: str) -> str:
    dt = _parse_ts(iso_ts)
    return dt.strftime("%Y-%m-%d") if dt else (iso_ts or "未知时间")


def _fmt_hit(h: Hit, idx: int) -> None:
    """统一的一条命中输出（含时间/成熟度/溯源/前提/坑/冲突提示）。"""
    age = _age_days(h.unit.timestamp)
    warn = f"  ⚠已过{age}天，可能过时" if age >= STALE_DAYS else ""
    src = {"main": "主库", "layer1": "遗忘层1", "conflict": "冲突对象",
           "mirror": "镜像(冷)"}.get(h.source, h.source)
    ctype = {"success": "✅成功", "fail": "⚠️踩坑",
             "neutral": "◻️中性"}.get(h.unit.case_type, h.unit.case_type)
    print(f"  {idx}. [{src}] {h.unit.unit_id} 相关={h.rel:.2f} 综合={h.score:.3f} "
          f"{ctype} 可信={round(h.unit.score, 2)} 成熟度={_maturity_label(h.unit.maturity)}")
    print(f"      摘要: {h.unit.summary}")
    if h.unit.source_ref:
        print(f"      溯源: {h.unit.source_ref}")
    if h.unit.premise:
        print(f"      前提: {h.unit.premise}")
    if h.unit.counter:
        print(f"      坑/限: {h.unit.counter}")
    if h.unit.content:
        print(f"      细节: {h.unit.content[:80]}")
    print(f"      时间: {_fmt_date(h.unit.timestamp)}{warn}")


def ops_remember(summary: str, detail: str = "", tags: str = "",
                 premise: str = "", counter: str = "", score: float = DEFAULT_SCORE,
                 case_type: str = CaseType.NEUTRAL, pinned: bool = False,
                 source_ref: str = "") -> None:
    sz = _ensure_default_library()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    if "经验" not in tag_list:
        tag_list.insert(0, "经验")
    unit = ingest_unit(sz.subzone_id, summary=summary, content=detail, tags=tag_list,
                       premise=premise, counter=counter, score=score, case_type=case_type,
                       pinned=pinned, source_ref=source_ref)
    print(f"✅ 经验已记录: {unit.unit_id}")
    print(f"   摘要: {unit.summary}")
    print(f"   标签: {'、'.join(unit.tags)}")
    print(f"   时间: {_fmt_date(unit.timestamp)}  可信度: {unit.score}  类型: {unit.case_type}"
          + ("  🔒重要(受保护)" if unit.pinned else ""))
    if premise:
        print(f"   适用前提: {premise}")
    if counter:
        print(f"   反面/坑: {counter}")
    if source_ref:
        print(f"   溯源: {source_ref}")
    print(f"   成熟度: {_maturity_label(unit.maturity)}（新经验为待验证，经 feedback 验证后自动升级）")


def ops_latest(n: int = 5) -> None:
    units = sorted(list_units(), key=lambda u: u.timestamp, reverse=True)
    if not units:
        print("记忆库还没有经验。遇到值得记的事，告诉 AI『记一下：……』即可沉淀。")
        return
    print(f"══ 最近 {min(n, len(units))} 条经验 ══")
    for i, u in enumerate(units[:n], 1):
        age = _age_days(u.timestamp)
        warn = f"  ⚠已过{age}天" if age >= STALE_DAYS else ""
        print(f"{i}. [{_fmt_date(u.timestamp)}]{warn} {u.unit_id}")
        print(f"   {u.summary}"
              + (f"  [{_maturity_label(u.maturity)}]" if u.maturity != Maturity.DRAFT else ""))


def ops_status() -> None:
    zones = load_zones()
    subzones = [s for s in load_subzones() if s.status == SubzoneStatus.ACTIVE]
    units = list(list_units())
    cold = list(list_cold_units())
    mirrors = load_mirror_subzones()
    pending = list_proposals(status=ProposalStatus.PENDING)
    print("══ 记忆库概览 ══")
    print(f"  大区 {len(zones)} 个: {', '.join(z.name for z in zones) or '无'}")
    print(f"  小区 {len(subzones)} 个（活跃）: {', '.join(s.name for s in subzones) or '无'}")
    print(f"  活跃经验 {len(units)} 条 | 冷库归档 {len(cold)} 条（镜像小区 {len(mirrors)} 个）")
    print(f"  待批提案 {len(pending)} 条")
    print(f"  数据位置: {DATA_DIR}")
    if units:
        latest = sorted(units, key=lambda u: u.timestamp, reverse=True)[:3]
        print("  最近经验:")
        for u in latest:
            print(f"    · [{_fmt_date(u.timestamp)}] {u.summary[:50]}")


def ops_remove(unit_id: str) -> int:
    u = load_unit(unit_id)
    if u is None:
        print(f"主库中不存在该经验: {unit_id}（请用 latest 查看正确 ID）")
        return 1
    if u.pinned:
        print(f"🔒 经验 {unit_id} 已被标记为【重要/受保护】，禁止直接删除。")
        print(f"   如确实要删，先解除保护: python memory_onefile.py augment --id {unit_id} --unpin")
        return 1
    delete_unit(unit_id)
    print(f"🗑 已删除主库经验: {unit_id}（{u.summary[:40]}）")
    print("  （版本快照仍保留在遗忘层1，可回溯）")
    return 0


# ===========================================================================
# 九、CLI
# ===========================================================================
def _split_csv(s) -> list:
    if not s:
        return []
    return [x.strip() for x in str(s).split(",") if x.strip()]


def cmd_init(_):
    ensure_dirs()
    print(f"数据目录已就绪: {DATA_DIR}")


def cmd_status(_):
    ops_status()


def cmd_zone_list(_):
    for z in load_zones():
        print(f"{z.zone_id}  {z.name}  标签: {','.join(z.global_tags) or '无'}  备注: {z.note or '-'}")


def cmd_zone_create(a):
    z = create_zone(a.name, _split_csv(a.tags), a.note or "")
    print(f"已创建大区: {z.zone_id}  {z.name}")


def cmd_zone_delete(a):
    delete_zone(a.zone_id)
    print(f"已删除大区: {a.zone_id}（并解除其在各小区的挂载）")


def cmd_subzone_list(_):
    zmap = {z.zone_id: z.name for z in load_zones()}
    for s in load_subzones():
        zones = ", ".join(zmap.get(zid, zid) for zid in s.zone_ids) or "未挂大区"
        print(f"{s.subzone_id}  {s.name}  [{s.status}]  挂载: {zones}  "
              f"routes: {','.join(s.routes) or '-'}  active: {s.active_route or '-'}")
    if not load_subzones():
        print("（还没有小区）")


def cmd_subzone_create(a):
    sz = create_subzone(a.name, zone_ids=a.zone, tags=_split_csv(a.tags),
                        routes=_split_csv(a.routes), active_route=a.active_route,
                        note=a.note or "")
    print(f"已创建小区: {sz.subzone_id}  {sz.name}")


def cmd_subzone_dissolve(a):
    dissolve_subzone(a.subzone_id)
    print(f"已解散小区: {a.subzone_id}")


def cmd_ingest(a):
    unit = ingest_unit(subzone_id=a.subzone, summary=a.summary, content=a.content or "",
                       tags=_split_csv(a.tags), route=a.route)
    print(f"已摄入单元: {unit.unit_id} -> 小区 {unit.subzone_id}")
    print(f"  摘要: {unit.summary}  标签: {','.join(unit.tags) or '无'}")


def cmd_ingest_auto(a):
    """对话批量摄入：只处理**本次新建**的提案；自动批准仅在 light 模式下、且跳过冲突告警。

    早期实现会遍历"全部 pending INGEST 提案"，把 capture / import-dir 里等人审批的草稿
    一起自动入库，等于绕过人工审批闸，也违背 README 里"冲突告警仍走人工复核"的承诺。
    """
    plugin = IngestPlugin()
    segments = []
    for seg in a.segments:
        if "|" in seg:
            speaker, text = seg.split("|", 1)
        else:
            speaker, text = "用户", seg
        segments.append((speaker.strip(), text.strip()))
    props = plugin.scan_conversation(segments, default_subzone_id=a.subzone,
                                     review_mode=a.review, keep_original=a.keep_original)
    print(f"对话摄入插件产出 {len(props)} 条入库提案（审核模式: {a.review}）")
    if not props:
        return 0
    if not a.auto_approve:
        print("使用 `python memory_onefile.py proposals --status pending` 查看，"
              "`approve <id> --execute` 执行")
        return 0
    if a.review != "light":
        print("⚠ --auto-approve 只在 --review light 模式下生效；本次按保守模式落盘，等待人工审批。")
        print("  待批清单: python memory_onefile.py proposals --status pending")
        return 1
    executed, conflicted = 0, 0
    for p in props:                     # 只执行本次生成的提案
        if "冲突告警" in (p.reason or ""):
            conflicted += 1
            continue
        execute_proposal(p.proposal_id, auto_approve=True)
        executed += 1
    print(f"轻量模式自动批准并执行 {executed} 条"
          + (f"；冲突告警 {conflicted} 条转人工复核" if conflicted else ""))
    return 0


def cmd_capture(a):
    """一键收藏（P1 双通道）：显式标记 → 自动生成经验草稿提案 → 人工 approve 才入库。
    与 ingest-auto 构成双通道：capture 是"人先标记"、ingest-auto 是"对话文本批量扫描"，
    两者都汇入同一提案审批闸。"""
    sz = _ensure_default_library()
    tag_list = [t.strip() for t in a.tags.split(",") if t.strip()] if a.tags else []
    if "经验" not in tag_list:
        tag_list.insert(0, "经验")
    unit_data = {
        "subzone_id": sz.subzone_id,
        "summary": " ".join(a.summary).strip(),
        "content": a.detail or "",
        "tags": tag_list,
        "premise": a.premise or "",
        "counter": a.counter or "",
        "score": a.score if 0.0 <= a.score <= 1.0 else DEFAULT_SCORE,
        "case_type": a.case_type,
        "pinned": a.pin,
        "source_ref": a.source_ref or "",
        # maturity 不传 → 入库默认 draft（待验证）
    }
    if not unit_data["summary"]:
        print("摘要不能为空")
        return 1
    p = Proposal.create(ProposalKind.INGEST, "capture", {"unit": unit_data},
                        "用户一键收藏，等待人工审批入库")
    save_proposal(p)
    print(f"📥 已生成经验草稿提案: {p.proposal_id}")
    print(f"   摘要: {unit_data['summary']}")
    print(f"   标签: {'、'.join(tag_list)}")
    if unit_data["premise"]:
        print(f"   适用前提: {unit_data['premise']}")
    if unit_data["counter"]:
        print(f"   反面/坑: {unit_data['counter']}")
    if unit_data["source_ref"]:
        print(f"   溯源: {unit_data['source_ref']}")
    print("   成熟度: 待验证（审批入库后为 draft，经 feedback 验证后升级）")
    print("   → 人工确认后入库: python memory_onefile.py approve <id> --execute")
    print("   → 不需要就驳回: python memory_onefile.py reject <id> -r 原因")
    return 0


# ===================== markdown 双向迁移（P1）=====================
def _unit_md_text(u: Unit) -> str:
    """一条经验一个 .md：标题=摘要，头部=全部结构化元数据，--- 后=原文详情。
    人可读可改，改完可被 import-dir 反向导回（仍是结构化经验）。"""
    head = [
        f"unit_id: {u.unit_id}",
        f"subzone_id: {u.subzone_id}",
        f"tags: {','.join(u.tags)}",
        f"premise: {u.premise}",
        f"counter: {u.counter}",
        f"score: {u.score}",
        f"case_type: {u.case_type}",
        f"pinned: {u.pinned}",
        f"source_ref: {u.source_ref}",
        f"maturity: {u.maturity}",
        f"created_at: {u.timestamp}",
    ]
    return f"# {u.summary}\n\n" + "\n".join(head) + "\n---\n" + (u.content or "")


def _parse_unit_md(text: str) -> Optional[dict]:
    """解析导出格式：返回 unit 数据字典（含 subzone_id 由调用方决定是否采用）。"""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        return None
    summary = lines[0][2:].strip()
    fields: dict = {}
    body: List[str] = []
    in_head = True
    for ln in lines[1:]:
        if in_head:
            if ln.strip() == "---":
                in_head = False
                continue
            if ":" in ln:
                k, _, v = ln.partition(":")
                fields[k.strip()] = v.strip()
        else:
            body.append(ln)
    tags = [t.strip() for t in fields.get("tags", "").split(",") if t.strip()]
    try:
        score = float(fields.get("score", DEFAULT_SCORE))
    except ValueError:
        score = DEFAULT_SCORE
    case_type = fields.get("case_type", CaseType.NEUTRAL)
    if case_type not in (CaseType.SUCCESS, CaseType.FAIL, CaseType.NEUTRAL):
        case_type = CaseType.NEUTRAL
    maturity = fields.get("maturity", Maturity.DRAFT)
    if maturity not in (Maturity.DRAFT, Maturity.ACTIVE, Maturity.TRUSTED, Maturity.FLIPPED):
        maturity = Maturity.DRAFT
    pinned = str(fields.get("pinned", "false")).lower() == "true"
    return {
        "summary": summary, "content": "\n".join(body).strip(),
        "tags": tags, "premise": fields.get("premise", ""),
        "counter": fields.get("counter", ""), "score": score,
        "case_type": case_type, "pinned": pinned,
        "source_ref": fields.get("source_ref", ""), "maturity": maturity,
        "_src_unit_id": fields.get("unit_id", ""),
        "_src_subzone_id": fields.get("subzone_id", ""),
    }


def cmd_export_dir(a):
    units = sorted(list_units(), key=lambda u: u.unit_id)
    if not units:
        print("主库暂无经验可导出")
        return
    out_dir = Path(a.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for u in units:
        fname = out_dir / f"{u.unit_id}.md"
        fname.write_text(_unit_md_text(u), encoding="utf-8")
        written += 1
    print(f"已导出 {written} 条经验 -> {out_dir}")
    print("（每条一个 .md，人可读可改；改完可用 import-dir 反向导回）")


def cmd_import_dir(a):
    in_dir = Path(a.dir)
    if not in_dir.is_dir():
        print(f"目录不存在: {a.dir}")
        return 1
    default_sz = _ensure_default_library()
    by_name = {s.name: s for s in load_subzones() if s.status == SubzoneStatus.ACTIVE}
    existing_units = {u.summary for u in list_units()}
    existing_ids = {u.unit_id for u in list_units()}
    md_files = sorted(in_dir.glob("*.md"))
    if not md_files:
        print(f"目录内没有 .md 文件: {a.dir}")
        return 1
    created, dup_alerts, restored_subzone = 0, 0, 0
    for f in md_files:
        try:
            data = _parse_unit_md(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ⚠ 解析失败 {f.name}: {e}")
            continue
        if not data:
            print(f"  ⚠ 格式无法识别 {f.name}（需以 '# 摘要' 开头）")
            continue
        dup = []
        if data["_src_unit_id"] and data["_src_unit_id"] in existing_ids:
            dup.append("同 id 已存在于主库")
        if data["summary"] in existing_units:
            dup.append("摘要与已有经验相同")
        unit_data = {k: v for k, v in data.items() if not k.startswith("_")}
        # 尽量还原导出时的小区归属：目标小区仍活跃就用它，否则落默认库
        target = default_sz
        src_sz = get_subzone(data.get("_src_subzone_id", ""), active_only=True)
        if src_sz is not None:
            target = src_sz
            restored_subzone += 1
        unit_data["subzone_id"] = target.subzone_id
        reason = "import-dir 批量导入，等待人工审批"
        if dup:
            dup_alerts += 1
            reason = "import-dir 批量导入 ⚠ 可能重复: " + "；".join(dup) + "（不自动合并，等待人工判断）"
        p = Proposal.create(ProposalKind.INGEST, "import-dir", {"unit": unit_data}, reason)
        save_proposal(p)
        created += 1
    print(f"已生成 {created} 条导入提案（重复告警 {dup_alerts} 条，均走提案、不自动入库）")
    if restored_subzone:
        print(f"  其中 {restored_subzone} 条按导出时的原小区归属还原，其余落默认库「{default_sz.name}」")
    if created:
        print("  查看: python memory_onefile.py proposals --status pending")
        print("  入库: python memory_onefile.py approve <id> --execute（或一条条审）")
        print("  驳回: python memory_onefile.py reject <id> -r 原因")
    return 0


def _unit_to_json(u: Unit, source: str, score: float,
                  rel: Optional[float] = None) -> dict:
    znames = []
    sz = get_subzone(u.subzone_id)
    if sz:
        znames = [z.name for z in load_zones() if z.zone_id in sz.zone_ids]
    age = _age_days(u.timestamp)
    out = {
        "id": u.unit_id, "summary": u.summary, "detail": u.content,
        "premise": u.premise, "counter": u.counter, "conflict_ids": u.conflict_ids,
        "score": round(u.score, 2), "case_type": u.case_type, "tags": u.tags,
        "pinned": u.pinned,
        "source_ref": u.source_ref, "maturity": u.maturity,
        "maturity_label": _maturity_label(u.maturity),
        "subzone_id": u.subzone_id, "zone_names": znames,
        "timestamp": u.timestamp, "age_days": age, "stale": age >= STALE_DAYS,
        "source": source, "heat": round(u.active_score, 2),
    }
    if rel is not None:
        out["relevance"] = round(rel, 4)
    return out


def cmd_search(a):
    r = SearchEngine(use_semantic=not a.no_semantic).search(a.query, top_k=a.top_k, rerank=a.rerank)
    if a.json_out:
        out = {
            "query": a.query, "reranked": a.rerank, "semantic_used": r.semantic_used,
            "semantic_backend": r.semantic_backend,
            "gap": r.gap, "mirror_proposal_id": r.mirror_proposal_id,
            "stale_days": STALE_DAYS,
            "units": [_unit_to_json(h.unit, h.source, h.score, h.rel) for h in r.hot_hits],
            "cold_units": [_unit_to_json(h.unit, "mirror", h.score, h.rel)
                           for h in r.cold_hits],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"══ 常规检索: {a.query} ══")
    if not r.hot_hits:
        print("  热区（主库+遗忘层1）: 无召回")
    else:
        print(f"  热区召回 {len(r.hot_hits)} 条（重排: {'开' if a.rerank else '关'}，top_k={a.top_k}）:")
        hot_ids = {h.unit.unit_id for h in r.hot_hits}
        for idx, h in enumerate(r.hot_hits, 1):
            _fmt_hit(h, idx)
            confs = [c for c in h.unit.conflict_ids if c in hot_ids]
            if confs:
                print(f"      ⚠ 冲突: {', '.join(confs)}（成对返回，交由权衡，不静默取舍）")
    if r.gap:
        print(f"  [自省校验] 判定信息缺口，自动唤醒遗忘层2镜像检索（提案 {r.mirror_proposal_id}）")
        if r.cold_hits:
            print(f"  镜像只读召回 {len(r.cold_hits)} 条（临时载入，不拉出、不计数）:")
            for h in r.cold_hits:
                print(f"    [镜像(冷)] {h.unit.unit_id} 相关={h.rel:.2f}  摘要: {h.unit.summary}")
                if h.unit.source_ref:
                    print(f"        溯源: {h.unit.source_ref}")
        else:
            print("  镜像只读召回: 无命中（冷库无相关信息）")
    else:
        print("  [自省校验] 信息充足，无需唤醒遗忘层2")
    print(f"  语义检索: {'已启用(' + r.semantic_backend + ')' if r.semantic_used else '未启用/降级'}")
    return 0


def cmd_mirror_search(a):
    r = SearchEngine(use_semantic=False).mirror_search(a.query, top_k=a.top_k,
                                                       include_migrated=a.include_migrated)
    print(f"══ 遗忘层2 镜像检索（只读查阅）: {a.query} ══")
    if not r:
        print("  无命中")
    for h in r:
        print(f"  [镜像(冷)] {h.unit.unit_id} 分={h.score:.3f}  摘要: {h.unit.summary}")
    print("  (本操作不拉出、不修改状态、不计入解封次数)")


def cmd_freeze(a):
    frozen = freeze_units(a.unit)
    print(f"已封存 {len(frozen)} 个单元 -> 遗忘层2（镜像小区自动创建/更新）")
    for u in frozen:
        print(f"  - {u.unit_id}  摘要: {u.summary[:30]}")


def cmd_unseal(a):
    unit = unseal_unit(a.unit, auto_rebuild=not a.no_rebuild)
    layer = "遗忘层1" if unit.source_layer == "layer1" else "主库"
    print(f"已解封拉出: {unit.unit_id} -> {layer}")
    print(f"  解封计数器: {unit.access_unseal_count} / 阈值 {unit.migration_threshold}")
    if unit.access_unseal_count >= unit.migration_threshold:
        print("  ⚠ 已达标 → 运行 `python memory_onefile.py migrate-check` 生成永久回迁提案")


def cmd_migrate_check(_):
    props = HotMigrationPlugin().monitor()
    if not props:
        print("无可回迁单元（没有解封计数达标的冷单元）")
        return 0
    for p in props:
        print(f"{p.proposal_id}  {p.kind}  单元={p.payload['unit_id']}  原因: {p.reason}")
    print("（补一条：已有待批提案的单元不会重复生成）")
    return 0


def cmd_lifecycle(_):
    props = SubzoneLifecyclePlugin().produce()
    if not props:
        print("小区生命周期插件: 无解散提案（所有小区均持有活跃单元或已解散）")
        return 0
    for p in props:
        print(f"{p.proposal_id}  {p.kind}  小区={p.payload['subzone_id']}  原因: {p.reason}")
    return 0


def cmd_decay(_):
    print(f"已完成 {decay_active_scores()} 个活跃单元的按日衰减（幂等，不会跨日累乘）")
    return 0


def _housekeeping_scan(now=None):
    """housekeeping 核心巡检（只产提案、不执行）：返回 (衰减数, 新提案列表)。

    已存在待批冻结提案的单元会被跳过——早期每天都会为同一个闲置单元新生成一条，
    待批清单会被刷爆，人工反而无从审批。
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    n_decayed = decay_active_scores(now)
    candidates = []
    for unit in list_units():
        if unit.pinned:
            continue
        idle_days = _elapsed_days(unit.last_access_at, now)
        if idle_days is None:
            continue
        if unit.active_score < HOUSEKEEP_FREEZE_SCORE and idle_days >= HOUSEKEEP_FREEZE_IDLE_DAYS:
            candidates.append((unit, idle_days))
    created = []
    for unit, idle in candidates:
        if pending_proposal_exists(
                ProposalKind.FREEZE,
                lambda p, uid=unit.unit_id: uid in (p.payload.get("unit_ids") or [])):
            continue
        p = Proposal.create(ProposalKind.FREEZE, "housekeeping",
                            {"unit_ids": [unit.unit_id]},
                            f"低活跃({unit.active_score:.1f}) 闲置{idle:.0f}天，建议冷封存")
        save_proposal(p)
        created.append(p)
    return n_decayed, created


def cmd_housekeeping(_):
    """自动巡检：① 活跃度按日衰减 → ② 扫描低活跃+长期未访问单元（跳过 pinned）→
    ③ 生成冷封存提案（人工审批后执行）→ ④ 追加写 housekeeping.log。"""
    n_decayed, created = _housekeeping_scan()
    lines = [f"[{_iso_now()}] housekeeping: 衰减 {n_decayed} 条，建议冷封存 {len(created)} 条"]
    for p in created:
        lines.append(f"  {p.proposal_id} {p.kind} 单元={p.payload['unit_ids'][0]} 原因: {p.reason}")
    try:
        with open(DATA_DIR / HOUSEKEEP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"（写 housekeeping.log 失败: {e}）")
    print("══ 自动巡检 housekeeping ══")
    mat_counts = {}
    for unit in list_units():
        mat_counts[unit.maturity] = mat_counts.get(unit.maturity, 0) + 1
    dist = " · ".join(
        f"{_maturity_label(m)} {mat_counts.get(m, 0)}"
        for m in (Maturity.DRAFT, Maturity.ACTIVE, Maturity.TRUSTED, Maturity.FLIPPED))
    print(f"  成熟度分布: {dist or '（主库为空）'}")
    print(f"  ① 活跃度按日衰减: {n_decayed} 条")
    print(f"  ② 建议冷封存: {len(created)} 条（pinned 重要经验已跳过）")
    for p in created:
        u = load_unit(p.payload["unit_ids"][0])
        print(f"     - {p.proposal_id}  单元={p.payload['unit_ids'][0]}"
              + (f"  {u.summary[:30]}" if u else "") + f"  原因: {p.reason}")
    if created:
        print("  ③ 待批清单: python memory_onefile.py proposals --status pending")
        print("     批准并执行: python memory_onefile.py approve <id> --execute")
        print("     驳回: python memory_onefile.py reject <id> -r 原因")
    else:
        print("  ③ 无可冷封存单元（或全部为重要经验 / 已有待批提案）")
    print(f"  日志已追加: {DATA_DIR / HOUSEKEEP_LOG_FILE}")
    return 0


# ===========================================================================
# P2 serve：JSON-RPC 2.0 开放层（标准库手写；只读为主，写仅 propose）
# ===========================================================================
_RPC_READ_METHODS = {"ping", "search", "status", "get_unit", "list_units", "proposals"}
_RPC_PROPOSE_METHODS = {"capture", "housekeeping"}


def _rpc_int(params: dict, key: str, default: int, lo: int, hi: int) -> int:
    raw = params.get(key, default)
    if raw is None:
        raw = default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        raise MemoryModuleError(f"参数 {key} 必须是整数，收到 {raw!r}")
    return max(lo, min(hi, val))


def _rpc_capture(params: dict) -> dict:
    sz = _ensure_default_library()
    tags = [t.strip() for t in str(params.get("tags") or "").split(",") if t.strip()]
    if "经验" not in tags:
        tags.insert(0, "经验")
    try:
        score = float(params.get("score", DEFAULT_SCORE))
    except (TypeError, ValueError):
        score = DEFAULT_SCORE
    case_type = params.get("case_type") or CaseType.NEUTRAL
    if case_type not in (CaseType.SUCCESS, CaseType.FAIL, CaseType.NEUTRAL):
        raise MemoryModuleError("case_type 只能是 success/fail/neutral")
    unit_data = {
        "subzone_id": sz.subzone_id,
        "summary": str(params.get("summary") or "").strip(),
        "content": str(params.get("detail") or ""),
        "tags": tags,
        "premise": str(params.get("premise") or ""),
        "counter": str(params.get("counter") or ""),
        "score": score if 0.0 <= score <= 1.0 else DEFAULT_SCORE,
        "case_type": case_type,
        "pinned": bool(params.get("pin", False)),
        "source_ref": str(params.get("source_ref") or ""),
    }
    if not unit_data["summary"]:
        raise MemoryModuleError("summary 不能为空")
    p = Proposal.create(ProposalKind.INGEST, "serve", {"unit": unit_data},
                        "serve 端一键收藏提案，等待人工审批入库")
    save_proposal(p)
    return {"proposal_id": p.proposal_id, "kind": p.kind,
            "summary": unit_data["summary"], "status": "pending"}


def _rpc_dispatch(method: str, params: dict) -> dict:
    if method == "ping":
        return {"pong": True,
                "read": sorted(_RPC_READ_METHODS),
                "propose": sorted(_RPC_PROPOSE_METHODS),
                "note": "只读方法为主；写操作仅生成提案，无任何直接改库方法"}
    if method == "search":
        query = params.get("query", "")
        if not isinstance(query, str):
            raise MemoryModuleError("参数 query 必须是字符串")
        top_k = _rpc_int(params, "top_k", SEARCH_TOP_K, 1, MAX_TOP_K)
        r = SearchEngine(use_semantic=bool(params.get("semantic", True))).search(
            query, top_k=top_k, rerank=bool(params.get("rerank", True)))
        return {"query": r.query, "semantic_used": r.semantic_used,
                "semantic_backend": r.semantic_backend, "gap": r.gap,
                "units": [_unit_to_json(h.unit, h.source, h.score, h.rel)
                          for h in r.hot_hits],
                "cold_units": [_unit_to_json(h.unit, "mirror", h.score, h.rel)
                               for h in r.cold_hits]}
    if method == "status":
        return _audit_summary()
    if method == "get_unit":
        uid = params.get("unit_id", "")
        if not isinstance(uid, str) or not uid:
            raise MemoryModuleError("参数 unit_id 必须是非空字符串")
        u = load_unit(uid)
        if u is None:
            raise MemoryModuleError(f"单元不存在: {uid}")
        return _unit_to_json(u, "serve", u.score)
    if method == "list_units":
        items = []
        for u in sorted(list_units(), key=lambda x: x.unit_id):
            if params.get("maturity") and u.maturity != params["maturity"]:
                continue
            items.append({"id": u.unit_id, "summary": u.summary,
                          "maturity": u.maturity, "score": round(u.score, 2),
                          "pinned": u.pinned, "tags": u.tags,
                          "source_ref": u.source_ref})
        return {"total": len(items), "units": items}
    if method == "proposals":
        status = params.get("status", ProposalStatus.PENDING)
        if status not in (ProposalStatus.PENDING, ProposalStatus.APPROVED,
                          ProposalStatus.REJECTED, ProposalStatus.EXECUTED):
            status = ProposalStatus.PENDING
        props = list_proposals(status=status)
        return {"proposals": [
            {"proposal_id": p.proposal_id, "kind": p.kind, "plugin": p.plugin,
             "status": p.status, "reason": p.reason,
             "summary": p.payload.get("unit", {}).get("summary", "")
             if p.kind == ProposalKind.INGEST else ""}
            for p in props]}
    if method == "capture":
        return _rpc_capture(params)
    if method == "housekeeping":
        n_decayed, created = _housekeeping_scan()
        return {"decayed": n_decayed,
                "freeze_proposals": [p.proposal_id for p in created],
                "note": "只产冷封存提案，执行需 CLI approve"}
    raise MemoryModuleError(f"未知方法: {method}")


def _rpc_handle(req: dict) -> dict:
    rid = req.get("id")
    if req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
        return {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"},
                "id": rid}
    method = req["method"]
    if method not in _RPC_READ_METHODS | _RPC_PROPOSE_METHODS:
        return {"jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": rid}
    params = req.get("params") or {}
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params"},
                "id": rid}
    try:
        result = _rpc_dispatch(method, params)
        return {"jsonrpc": "2.0", "result": result, "id": rid}
    except MemoryModuleError as e:
        return {"jsonrpc": "2.0", "error": {"code": -32000, "message": str(e)}, "id": rid}
    except (TypeError, ValueError) as e:
        return {"jsonrpc": "2.0",
                "error": {"code": -32602, "message": f"Invalid params: {e}"}, "id": rid}
    except Exception as e:
        return {"jsonrpc": "2.0",
                "error": {"code": -32603, "message": f"Internal error: {e}"}, "id": rid}


def _serve_stdio():
    """一行一个 JSON-RPC 请求 → 一行一个响应；适合管道/嵌入/被其他程序调用。"""
    import sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            resp = {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"},
                    "id": None}
        else:
            resp = _rpc_handle(req)
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _serve_http(port: int):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, obj: dict):
            payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            try:
                req = json.loads(body.decode("utf-8"))
            except Exception:
                self._reply({"jsonrpc": "2.0",
                             "error": {"code": -32700, "message": "Parse error"}, "id": None})
                return
            self._reply(_rpc_handle(req))

        def do_GET(self):
            self._reply({"jsonrpc": "2.0", "result": {
                "service": "doubao-memory serve (P2)",
                "endpoint": "POST /rpc  (JSON-RPC 2.0)",
                "methods": sorted(_RPC_READ_METHODS | _RPC_PROPOSE_METHODS),
                "bind": "127.0.0.1 only"}, "id": 0})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"serve 已启动: http://127.0.0.1:{port}/rpc  （Ctrl+C 停止；仅本机可访问）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("serve 已停止")


def cmd_serve(a):
    if a.http:
        _serve_http(a.port)
    else:
        _serve_stdio()


# ===========================================================================
# P2 audit：记忆库审计（文本摘要 / --html 断网静态报告）
# ===========================================================================
def _audit_summary() -> dict:
    units = list(list_units())
    cold = list(list_cold_units())
    mirrors = load_mirror_subzones()
    pending = [p for p in list_proposals(status=ProposalStatus.PENDING)]
    subzones = [s for s in load_subzones() if s.status == SubzoneStatus.ACTIVE]
    mat = {m: 0 for m in (Maturity.DRAFT, Maturity.ACTIVE,
                           Maturity.TRUSTED, Maturity.FLIPPED)}
    pinned = 0
    for u in units:
        mat[u.maturity] = mat.get(u.maturity, 0) + 1
        if u.pinned:
            pinned += 1
    dist = " · ".join(f"{_maturity_label(m)} {mat[m]}"
                      for m in (Maturity.DRAFT, Maturity.ACTIVE,
                                Maturity.TRUSTED, Maturity.FLIPPED))
    return {"units": len(units), "cold": len(cold), "mirrors": len(mirrors),
            "subzones": len(subzones), "pending": len(pending), "pinned": pinned,
            "maturity": mat, "maturity_text": dist, "data_dir": str(DATA_DIR)}


def _audit_html() -> str:
    s = _audit_summary()
    units = sorted(list_units(), key=lambda u: u.timestamp, reverse=True)
    pending = [p for p in list_proposals(status=ProposalStatus.PENDING)]
    log_lines: List[str] = []
    try:
        logf = DATA_DIR / HOUSEKEEP_LOG_FILE
        if logf.exists():
            log_lines = logf.read_text(encoding="utf-8").splitlines()[-10:]
    except Exception:
        pass

    def esc(x) -> str:
        return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    rows = "\n".join(
        f"<tr><td>{esc(u.unit_id)}</td><td>{esc(u.summary)}</td>"
        f"<td>{esc(_maturity_label(u.maturity))}</td><td>{u.score:.2f}</td>"
        f"<td>{esc(u.source_ref or '-')}</td><td>{'🔒' if u.pinned else ''}</td></tr>"
        for u in units)
    prows = "\n".join(
        f"<tr><td>{esc(p.proposal_id)}</td><td>{esc(p.kind)}</td>"
        f"<td>{esc(p.plugin)}</td>"
        f"<td>{esc(p.payload.get('unit', {}).get('summary', '')) if p.kind == ProposalKind.INGEST else ''}</td>"
        f"<td>{esc(p.reason)}</td></tr>" for p in pending)
    mat = s["maturity"]
    total = s["units"] or 1

    def bar(m: str, label: str) -> str:
        pct = mat.get(m, 0) / total * 100
        return (f'<div class="barl"><span>{esc(label)} {mat.get(m, 0)} 条</span>'
                f'<div class="bar"><span style="width:{pct:.1f}%"></span></div></div>')

    css = ("body{font-family:system-ui,-apple-system,'Microsoft YaHei',sans-serif;"
           "margin:0;background:#f5f6f8;color:#1f2328}"
           ".wrap{max-width:960px;margin:0 auto;padding:24px 16px}"
           "h1{font-size:22px;margin:0 0 4px}.sub{color:#666;font-size:13px;margin-bottom:20px}"
           ".cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px}"
           ".card{background:#fff;border:1px solid #e4e6eb;border-radius:10px;"
           "padding:14px 18px;min-width:130px;flex:1}"
           ".card b{display:block;font-size:24px}.card span{color:#666;font-size:13px}"
           "h2{font-size:16px;margin:24px 0 10px;border-left:4px solid #3370ff;padding-left:8px}"
           ".barl{font-size:12px;color:#555;margin:6px 0}"
           ".bar{background:#eceff3;border-radius:6px;height:10px;overflow:hidden;margin:2px 0}"
           ".bar span{display:block;height:100%;background:#3370ff}"
           "table{width:100%;border-collapse:collapse;background:#fff;"
           "border:1px solid #e4e6eb;border-radius:10px;overflow:hidden;font-size:13px}"
           "th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #eef0f3}"
           "th{background:#fafbfc;color:#555}tr:last-child td{border-bottom:none}"
           "pre{background:#1f2328;color:#d0d7de;padding:12px;border-radius:8px;"
           "font-size:12px;overflow:auto}")
    return f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>doubao-memory 记忆库审计报告</title><style>{css}</style></head>
<body><div class="wrap">
<h1>doubao-memory 记忆库审计报告</h1>
<div class="sub">生成时间: {_iso_now()} · 数据位置: {esc(s['data_dir'])} · 本地生成、零外链、断网可开</div>
<div class="cards">
<div class="card"><b>{s['units']}</b><span>主库经验</span></div>
<div class="card"><b>{s['cold']}</b><span>冷库归档（镜像小区 {s['mirrors']}）</span></div>
<div class="card"><b>{s['pending']}</b><span>待批提案</span></div>
<div class="card"><b>{s['pinned']}</b><span>🔒 重要经验</span></div>
</div>
<h2>成熟度分布</h2>
{bar('draft', '待验证')}{bar('active', '已验证')}{bar('trusted', '高可信')}{bar('flipped', '已翻车')}
<h2>待批提案（{len(pending)}）</h2>
<table><tr><th>提案 ID</th><th>类型</th><th>来源</th><th>摘要</th><th>原因</th></tr>
{prows if prows else '<tr><td colspan=5>无待批提案</td></tr>'}
</table>
<h2>经验清单（{len(units)} 条，按时间倒序）</h2>
<table><tr><th>单元 ID</th><th>摘要</th><th>成熟度</th><th>可信度</th><th>溯源</th><th>保护</th></tr>
{rows if rows else '<tr><td colspan=6>主库暂无经验</td></tr>'}
</table>
<h2>housekeeping 巡检日志（最近 {len(log_lines)} 行）</h2>
<pre>{esc(chr(10).join(log_lines)) if log_lines else '（暂无巡检记录）'}</pre>
</div></body></html>"""


def cmd_audit(a):
    if a.html is None:                     # 完全没给 --html → 文本摘要
        s = _audit_summary()
        print("══ 记忆库审计 ══")
        print(f"  主库经验 {s['units']} 条 | 冷库 {s['cold']} 条（镜像小区 {s['mirrors']} 个）"
              f"| 待批提案 {s['pending']} 条 | 🔒 重要 {s['pinned']} 条")
        print(f"  成熟度: {s['maturity_text']}")
        print("  生成 HTML 报告: python memory_onefile.py audit --html [输出路径]"
              "（省略路径则写到 data/audit_report.html）")
        return 0
    out = Path(a.html) if a.html else DATA_DIR / "audit_report.html"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    out.write_text(_audit_html(), encoding="utf-8")
    print(f"审计报告已生成: {out}")
    print("（单文件、零外链、断网可开；主形态仍是插件+CLI，报告只是导出物）")
    return 0


def cmd_proposals(a):
    if getattr(a, "prune", False):
        removed = prune_proposals(a.days)
        print(f"已清理 {removed} 个已结束提案（executed/rejected 且文件超过 {a.days} 天未变动）")
        return 0
    props = list_proposals(status=a.status)
    if not props:
        print("无提案")
        return 0
    for p in props:
        print(f"{p.proposal_id}  [{p.status}]  {p.kind}  by {p.plugin}  @ {p.created_at}")
        if p.reason:
            print(f"    原因: {p.reason}")
        if p.kind == ProposalKind.INGEST:
            print(f"    摘要: {p.payload.get('unit', {}).get('summary', '')}")
    return 0


def cmd_approve(a):
    p = load_proposal(a.proposal_id)
    if p is None:
        print(f"提案不存在: {a.proposal_id}")
        return 1
    if p.status in (ProposalStatus.EXECUTED, ProposalStatus.REJECTED):
        print(f"提案已是 {p.status} 状态，无法再操作: {p.proposal_id}")
        return 1
    if not a.execute:
        if p.status == ProposalStatus.PENDING:
            approve_proposal(p)
        print(f"提案 {p.proposal_id} 已批准（使用 --execute 执行；重复批准是安全的）")
        return 0
    if p.kind == ProposalKind.MIRROR_SEARCH:
        if p.status == ProposalStatus.PENDING:
            approve_proposal(p)
        p.status = ProposalStatus.EXECUTED
        save_proposal(p)
        print(f"镜像检索提案 {p.proposal_id} 已完成（只读查阅，无数据变更）")
        return 0
    result = execute_proposal(a.proposal_id, auto_approve=True)
    print(f"提案 {a.proposal_id} 已批准并执行: {result}")
    return 0


def cmd_reject(a):
    p = load_proposal(a.proposal_id)
    if p is None:
        print(f"提案不存在: {a.proposal_id}")
        return 1
    reject_proposal(p, a.reason)
    print(f"提案 {a.proposal_id} 已驳回")
    return 0


def cmd_demo(_):
    """演示完整生命周期：摄入 → 检索 → 无关查询缺口 → 封存 → 镜像检索 → 解封 → 回迁。"""
    print("══ 完整生命周期演示 ══")
    zone = create_zone("演示大区", ["demo"], "仅用于生命周期演示")
    print(f"[1] 创建大区 {zone.zone_id}")
    sz = create_subzone("演示小区", [zone.zone_id], ["剧情", "设定"], ["r1"], "r1")
    print(f"[2] 创建小区 {sz.subzone_id}")
    u1 = ingest_unit(sz.subzone_id, "主角设定：林小满，18岁，高三转校生",
                     "林小满，18岁，高三转校生，性格外冷内热。", ["人物", "设定"])
    u2 = ingest_unit(sz.subzone_id, "关键约定：每周五更新章节", "每周五 20:00 更新一章。",
                     ["约定", "更新"])
    print(f"[3] 摄入单元 {u1.unit_id}, {u2.unit_id}")
    r = SearchEngine(use_semantic=False).search("主角是谁")
    print(f"[4] 常规检索『主角是谁』 → 热区 {len(r.hot_hits)} 条，最高相关 "
          f"{max([h.rel for h in r.hot_hits], default=0):.2f}，缺口={r.gap}")
    r0 = SearchEngine(use_semantic=False).search("量子计算 芯片 制程")
    print(f"[5] 无关查询『量子计算 芯片 制程』 → 热区 {len(r0.hot_hits)} 条（应为 0），"
          f"缺口={r0.gap}（应为 True，进而唤醒遗忘层2）")
    print("[6] 模拟长期闲置 → 封存两个单元")
    freeze_units([u1.unit_id, u2.unit_id])
    cold = load_cold_unit(u1.unit_id)
    print(f"    冷库确认: {cold is not None}, migration_mark={cold.migration_mark}")
    r2 = SearchEngine(use_semantic=False).search("主角是谁")
    print(f"[7] 封存后检索『主角是谁』 → 热区 {len(r2.hot_hits)} 条, 缺口={r2.gap}, "
          f"镜像召回 {len(r2.cold_hits)} 条（只读，不计数）")
    u1b = unseal_unit(u1.unit_id)
    print(f"[8] 解封拉出 {u1b.unit_id} → 计数 {u1b.access_unseal_count}/{u1b.migration_threshold}")
    props = HotMigrationPlugin().monitor()
    print(f"[9] 热度回迁插件监控: 提案 {len(props)} 条 (计数未达阈值时为空)")
    print("══ 演示结束 ══")
    print("提示：demo 会写入演示数据，正式使用请在自己的库上跑 remember/search。")
    return 0


# ---------------------------------------------------------------------------
# 经验增强命令 + LLM 接入层（agent：先学经验，再输出）
# ---------------------------------------------------------------------------
def _llm_available() -> bool:
    return bool(LLM_API_KEY and LLM_BASE_URL)


def _llm_chat(messages: list, temperature: float = 0.4) -> str:
    import requests
    r = requests.post(
        f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
        json={"model": LLM_MODEL, "messages": messages, "temperature": temperature},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def cmd_augment(a):
    unit = load_unit(a.unit_id)
    if unit is None:
        print(f"主库单元不存在: {a.unit_id}")
        return 1
    if a.premise is not None:
        unit.premise = a.premise
    if a.counter is not None:
        unit.counter = a.counter
    if a.score is not None:
        if not (0.0 <= a.score <= 1.0):
            print(f"score 必须在 0~1 之间: {a.score}")
            return 1
        unit.score = a.score
    if a.case_type is not None:
        if a.case_type not in (CaseType.SUCCESS, CaseType.FAIL, CaseType.NEUTRAL):
            print("case_type 只能是 success/fail/neutral")
            return 1
        unit.case_type = a.case_type
    if a.pin:
        unit.pinned = True
    if a.unpin:
        unit.pinned = False
    if a.source_ref is not None:
        unit.source_ref = a.source_ref
    unit.version += 1
    save_unit(unit)
    save_snapshot(unit, version=unit.version)
    print(f"已增强经验 {unit.unit_id}（版本 v{unit.version}，新快照已写入遗忘层1）:")
    print(f"  适用前提: {unit.premise or '（空）'}")
    print(f"  反面/坑: {unit.counter or '（空）'}")
    print(f"  可信度: {unit.score}  类型: {unit.case_type}"
          + ("  🔒重要(受保护)" if unit.pinned else ""))
    print(f"  成熟度: {_maturity_label(unit.maturity)}"
          + (f"  溯源: {unit.source_ref}" if unit.source_ref else ""))


def cmd_conflict_bind(a):
    ua = load_unit(a.id_a)
    ub = load_unit(a.id_b)
    if ua is None or ub is None:
        print("两条经验必须都存在主库中")
        return 1
    for u, other in ((ua, ub.unit_id), (ub, ua.unit_id)):
        if other not in u.conflict_ids:
            u.conflict_ids.append(other)
    for u in (ua, ub):
        u.version += 1
    save_unit(ua)
    save_unit(ub)
    save_snapshot(ua, version=ua.version)
    save_snapshot(ub, version=ub.version)
    print(f"已双向标记冲突: {ua.unit_id} <-> {ub.unit_id}（均已写入新版本快照）")
    print(f"  {ua.unit_id}: {ua.summary[:40]}")
    print(f"  {ub.unit_id}: {ub.summary[:40]}")
    print("（检索时会成对返回，交由 AI / 你权衡，不静默删除任一条）")
    return 0


def cmd_feedback(a):
    unit = load_unit(a.unit_id)
    if unit is None:
        print(f"主库单元不存在: {a.unit_id}")
        return 1
    if a.result not in ("good", "bad", "neutral"):
        print("result 只能是 good / bad / neutral")
        return 1
    delta = {"good": 0.10, "bad": -0.15, "neutral": 0.0}[a.result]
    old = unit.score
    unit.score = min(1.0, max(0.0, unit.score + delta))
    # 成熟度状态机：全由人工 feedback 推进（AI 不自动改记忆）
    old_maturity = unit.maturity
    if a.result == "good":
        if unit.maturity == Maturity.DRAFT:
            unit.maturity = Maturity.ACTIVE       # 首次验证有效：待验证 → 已验证
        elif unit.maturity == Maturity.ACTIVE:
            unit.maturity = Maturity.TRUSTED      # 再次验证有效：已验证 → 高可信
        # trusted 保持高可信
    elif a.result == "bad":
        unit.maturity = Maturity.FLIPPED          # 翻车：任何状态 → 已翻车（保留作风险提醒）
    unit.version += 1
    save_unit(unit)
    save_snapshot(unit, version=unit.version)
    append_feedback(a.unit_id, a.result, a.note or "")
    print(f"已记录反馈 [{a.result}] -> {a.unit_id}（版本 v{unit.version}，新快照已写入遗忘层1）")
    print(f"  可信度: {old:.2f} -> {unit.score:.2f}")
    if unit.maturity != old_maturity:
        print(f"  成熟度: {_maturity_label(old_maturity)} -> {_maturity_label(unit.maturity)}")
    else:
        print(f"  成熟度: {_maturity_label(unit.maturity)}")
    print(f"  备注: {a.note or '（无）'}")
    print("（反馈动态调整 score 与成熟度：验证有效的经验，下次检索排更前；翻车经验保留作风险提醒）")
    return 0


def cmd_export(a):
    unit = load_unit(a.unit_id)
    if unit is None:
        print(f"主库单元不存在: {a.unit_id}")
        return 1
    d = unit.to_dict()
    d["snapshots"] = len(load_snapshots(a.unit_id))
    print(json.dumps(d, ensure_ascii=False, indent=2))
    return 0


def _fmt_units_for_prompt(hits: List[Hit]) -> str:
    lines = []
    for i, h in enumerate(hits, 1):
        u = h.unit
        lines.append(f"[{i}] id={u.unit_id} 类型={u.case_type} 可信度={round(u.score, 2)} "
                     f"成熟度={_maturity_label(u.maturity)}")
        lines.append(f"    summary: {u.summary}")
        if u.source_ref:
            lines.append(f"    溯源: {u.source_ref}")
        if u.premise:
            lines.append(f"    premise 适用前提: {u.premise}")
        if u.counter:
            lines.append(f"    counter 不适用/坑: {u.counter}")
        if u.conflict_ids:
            lines.append(f"    conflict 冲突: {', '.join(u.conflict_ids)}")
        if u.content:
            lines.append(f"    detail: {u.content[:200]}")
    return "\n".join(lines)


LEARN_PROMPT_TMPL = """【原始用户任务】 {task}
【我准备使用的处理思路】 {plan}
【检索得到的历史经验单元】 {units}

你的工作：学习、梳理上面这批经验。严禁输出最终方案/成品回答！
要求：
1. 逐条提炼核心做法、适用前提 premise、不适用场景 counter、风险坑点
2. 识别经验之间的冲突点，写明各自取舍
3. 筛选适配本次任务条件的经验，剔除条件明显不匹配的
4. 输出一份精简的【经验认知总结】"""

BUILD_PROMPT_TMPL = """用户原始需求： {task}
你刚刚学习、归纳完成的经验认知： {knowledge}

现在正式完成本次工作。
规则：
1. 融合经验独立思考，禁止直接摘抄记忆原文
2. 优先选用前提匹配的经验；存在冲突方案时，写明不同路线的利弊与适用条件
3. 明确标注风险、限制条件
4. 结尾附上【经验溯源清单】，列出实际用到的记忆单元 ID"""


def cmd_agent(a):
    task = " ".join(a.task)
    if not _llm_available():
        print("⚠ 未配置 LLM（需设置环境变量 MEMORY_LLM_API_KEY / MEMORY_LLM_BASE_URL / MEMORY_LLM_MODEL）。")
        print("以下输出两段式提示词模板，可粘贴给任意 AI 使用：\n")
        r = SearchEngine(use_semantic=True).search(task, top_k=a.top, rerank=True)
        units_txt = _fmt_units_for_prompt(r.hot_hits + r.cold_hits)
        print("── 第一段：学习阶段（检索结果传入，只归纳不产出）──")
        print(LEARN_PROMPT_TMPL.format(
            task=task, plan="（请自行补充：你打算用什么思路/方法处理这个任务，将作为检索主 Query）",
            units=units_txt or "（无相关记忆）"))
        print("\n── 第二段：生成阶段（把学习总结传入）──")
        print(BUILD_PROMPT_TMPL.format(task=task, knowledge="{上一步的学习总结}"))
        print("\n溯源提示：本次召回单元 ID（附溯源与成熟度）:")
        for h in r.hot_hits + r.cold_hits:
            print(f"  - {h.unit.unit_id}  {h.unit.summary[:40]}"
                  + (f"  溯源: {h.unit.source_ref}" if h.unit.source_ref else ""))
        return 0
    plan = _llm_chat([
        {"role": "system",
         "content": "你只输出本次任务的解决思路、计划方法，不要最终答案。简洁描述你打算怎么处理这个需求。"},
        {"role": "user", "content": task},
    ])
    print("【1/4 自动构思处理思路】\n" + plan + "\n" + "-" * 60)
    r = SearchEngine(use_semantic=True).search(plan, top_k=a.top, rerank=True)
    units_txt = _fmt_units_for_prompt(r.hot_hits + r.cold_hits)
    if not units_txt:
        print("⚠ 没有检索到相关经验，直接生成（不带记忆）")
        final = _llm_chat([{"role": "user", "content": task}])
        print("\n===== 最终输出 =====\n" + final)
        return 0
    print(f"【2/4 检索经验】召回 {len(r.hot_hits) + len(r.cold_hits)} 条（重排+冲突打包）")
    if a.learn:
        knowledge = _llm_chat([
            {"role": "system", "content": "你现在是经验消化器，只做归纳理解，不交付最终工作。"},
            {"role": "user", "content": LEARN_PROMPT_TMPL.format(task=task, plan=plan, units=units_txt)},
        ])
        print("\n【3/4 经验内化总结】\n" + knowledge + "\n" + "-" * 60)
        build_user = BUILD_PROMPT_TMPL.format(task=task, knowledge=knowledge)
    else:
        build_user = (f"用户原始需求：{task}\n\n以下是检索到的相关历史经验，请参考并完成工作，"
                      f"结尾附【经验溯源清单】：\n{units_txt}")
    final = _llm_chat([
        {"role": "system", "content": "你是执行者，结合经验独立输出高质量成果。"},
        {"role": "user", "content": build_user},
    ])
    print("\n【4/4 最终输出】\n" + final)
    print("\n── 经验溯源清单 ──")
    for h in r.hot_hits + r.cold_hits:
        print(f"  {h.unit.unit_id}  {h.unit.summary[:40]}")
    print("落地后可反馈效果: python memory_onefile.py feedback --id <id> --result good|bad --note 备注")
    return 0


# ---------------------------------------------------------------------------
# 内置自检（子进程 + 临时目录隔离）
# ---------------------------------------------------------------------------
def _selftest_run() -> int:
    """子进程内执行：环境变量 MEMORY_DATA_DIR 已指向临时目录。

    覆盖：框架/摄入/检索/top_k 上限/无关查询不召回/自省缺口/封存镜像/只读约束/
    解封计数/回迁不覆盖/重复封存/解散拦截（主库与遗忘层1）/提案去重/衰减幂等/
    自动批准审批闸/朴素时间戳容错。
    """
    zone = create_zone("自检大区", ["selftest"])
    sz = create_subzone("自检小区", [zone.zone_id], ["剧情"], ["r1"], "r1")
    checks = []

    def ck(name, fn):
        try:
            fn()
            checks.append((name, True, ""))
        except Exception as e:
            checks.append((name, False, f"{type(e).__name__}: {e}"))

    def assert_true(cond, msg):
        if not cond:
            raise AssertionError(msg)

    u = ingest_unit(sz.subzone_id, "主角林小满 18 岁高三转校生", "外冷内热", ["人物", "设定"])
    ck("01 框架创建", lambda: assert_true(get_zone(zone.zone_id) is not None
                                          and get_subzone(sz.subzone_id) is not None, "未落盘"))
    ck("02 摄入+初始快照", lambda: assert_true(load_unit(u.unit_id) is not None
                                              and len(load_snapshots(u.unit_id)) == 1, "快照缺失"))
    ck("03 常规检索召回", lambda: assert_true(
        any(h.unit.unit_id == u.unit_id for h in SearchEngine(use_semantic=False).search("林小满").hot_hits),
        "未召回"))

    # —— 04 造 8 条同质单元，验证 top_k 是严格上限（修复前会放大到 3 倍/2 倍）——
    batch = [ingest_unit(sz.subzone_id, f"批量背景设定{i} 关键词坤", "", ["批量"]) for i in range(8)]
    ck("04 top_k 严格上限", lambda: assert_true(
        len(SearchEngine(use_semantic=False).search("关键词坤", top_k=3).hot_hits) <= 3,
        "返回条数超过 top_k"))
    ck("05 无关查询零召回+缺口触发", lambda: (
        lambda r: assert_true(not r.hot_hits and r.gap is True,
                              f"无关查询仍召回 {len(r.hot_hits)} 条 / gap={r.gap}"))(
        SearchEngine(use_semantic=False).search("量子退火 超导磁通量", top_k=5)))

    ck("06 封存+镜像小区", lambda: (
        freeze_units([u.unit_id]),
        assert_true(load_unit(u.unit_id) is None and load_cold_unit(u.unit_id) is not None
                    and any(m.subzone_id == sz.subzone_id for m in load_mirror_subzones()), "封存异常")))
    ck("07 缺口→镜像只读", lambda: (
        lambda r: assert_true(r.gap is True and any(h.unit.unit_id == u.unit_id for h in r.cold_hits)
                              and load_cold_unit(u.unit_id).migration_mark == ColdMark.NONE
                              and load_cold_unit(u.unit_id).unit.access_unseal_count == 0,
                              "只读约束被破坏"))(
        SearchEngine(use_semantic=False).search("林小满")))
    ck("08 解封计数+标记", lambda: (
        unseal_unit(u.unit_id),
        assert_true(load_cold_unit(u.unit_id).migration_mark == ColdMark.MIGRATED
                    and load_unit(u.unit_id).access_unseal_count == 1, "解封异常")))

    def _migrate_keeps_new_edit():
        live = load_unit(u.unit_id)
        live.summary = "解封后我改过的新摘要"
        live.premise = "新前提"
        live.version += 1
        save_unit(live)
        permanent_migrate(u.unit_id, force=True)
        after = load_unit(u.unit_id)
        assert_true(after.summary == "解封后我改过的新摘要" and after.premise == "新前提",
                    "回迁把主库新改动覆盖回旧副本了")
        assert_true(load_cold_unit(u.unit_id).migration_mark == ColdMark.PERMANENT, "回迁标记异常")
    ck("09 永久回迁不覆盖主库新改动", _migrate_keeps_new_edit)

    def _freeze_dedupe():
        frozen = freeze_units([u.unit_id, u.unit_id])
        assert_true(len(frozen) == 1, f"重复 unit 被封存了 {len(frozen)} 次")
    ck("10 重复封存去重", _freeze_dedupe)

    def _dissolve_blocked_main():
        sz2 = create_subzone("自检小区2", [zone.zone_id])
        ingest_unit(sz2.subzone_id, "自检活跃单元", "")
        assert_true(_raises_memory(dissolve_subzone, sz2.subzone_id), "主库活跃单元未拦截")
    ck("11 解散硬拦截（主库）", _dissolve_blocked_main)

    def _dissolve_blocked_layer1():
        sz3 = create_subzone("自检小区3", [zone.zone_id])
        u3 = ingest_unit(sz3.subzone_id, "遗忘层1 驻留单元", "", ["层1"], source_layer="layer1")
        freeze_units([u3.unit_id])
        unseal_unit(u3.unit_id)          # 溯源自 layer1 → 落 resident_units
        assert_true(load_resident_unit(u3.unit_id) is not None, "未落到遗忘层1 驻留区")
        assert_true(_raises_memory(dissolve_subzone, sz3.subzone_id), "遗忘层1 驻留单元未拦截")
    ck("12 解散硬拦截（遗忘层1）", _dissolve_blocked_layer1)

    def _housekeep_dedupe():
        u13 = ingest_unit(sz.subzone_id, "长期闲置自检单元", "", ["闲置"])
        d = load_unit(u13.unit_id)
        d.last_access_at = (datetime.datetime.now(datetime.timezone.utc)
                            - datetime.timedelta(days=200)).isoformat(timespec="seconds")
        d.active_score = 0.1
        save_unit(d)
        now = datetime.datetime.now(datetime.timezone.utc)
        _n1, c1 = _housekeeping_scan(now)
        _n2, c2 = _housekeeping_scan(now)
        assert_true(any(u13.unit_id in p.payload["unit_ids"] for p in c1), "未生成冷封存提案")
        assert_true(len(c2) == 0, f"重复生成了 {len(c2)} 条冷封存提案")
    ck("13 housekeeping 提案去重", _housekeep_dedupe)

    def _decay_idempotent():
        u14 = ingest_unit(sz.subzone_id, "衰减幂等自检单元 关键词坎", "", ["衰减"])
        d = load_unit(u14.unit_id)
        now = datetime.datetime.now(datetime.timezone.utc)
        d.last_access_at = (now - datetime.timedelta(days=20)).isoformat(timespec="seconds")
        save_unit(d)
        decay_active_scores(now)
        first = load_unit(u14.unit_id).active_score
        decay_active_scores(now)
        second = load_unit(u14.unit_id).active_score
        assert_true(abs(first - second) < 1e-6, f"衰减重复累乘: {first} -> {second}")
        assert_true(second <= ACTIVE_BASE_SCORE, "衰减结果异常")
    ck("14 衰减幂等（不跨日累乘）", _decay_idempotent)

    def _auto_approve_gate():
        ns = types.SimpleNamespace
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_capture(ns(summary=["无关的收藏草稿"], detail="", tags="", premise="",
                           counter="", score=0.6, case_type="neutral", pin=False,
                           source_ref=""))
        draft = [p for p in list_proposals(ProposalStatus.PENDING)
                 if p.kind == ProposalKind.INGEST
                 and p.payload.get("unit", {}).get("summary") == "无关的收藏草稿"]
        assert_true(len(draft) == 1, "capture 未生成草稿提案")
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_ingest_auto(ns(segments=["用户|关键参数：电压 220V 频率 50Hz 必须确认"],
                               subzone=sz.subzone_id, review="conservative",
                               keep_original=False, auto_approve=True))
        assert_true(load_proposal(draft[0].proposal_id).status == ProposalStatus.PENDING,
                    "自动批准把无关草稿一并入库了")
        assert_true(not any(x.summary == "无关的收藏草稿" for x in list_units()),
                    "无关草稿被自动写进主库")
    ck("15 自动批准不越过审批闸", _auto_approve_gate)

    def _naive_timestamp():
        u16 = ingest_unit(sz.subzone_id, "朴素时间戳自检单元", "", ["时间"])
        d = load_unit(u16.unit_id)
        d.last_access_at = "2026-01-01T00:00:00"      # 无时区（历史/手改数据）
        save_unit(d)
        decay_active_scores()
        _housekeeping_scan()
        delete_unit(u16.unit_id)
    ck("16 无时区时间戳不崩", _naive_timestamp)

    ok = 0
    print("══ 记忆模块内置自检（单文件版）══")
    print(f"  隔离数据目录: {DATA_DIR}")
    for name, passed, detail in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f"  <{detail}>" if detail else ""))
        ok += 1 if passed else 0
    print(f"  结果: {ok}/{len(checks)} 通过")
    return 0 if ok == len(checks) else 1


def _raises_memory(fn, *args) -> bool:
    try:
        fn(*args)
        return False
    except MemoryModuleError:
        return True


def _selftest_subprocess(tmp: str):
    env = {**os.environ, "MEMORY_DATA_DIR": tmp, "PYTHONIOENCODING": "utf-8",
           "TMP": tmp, "TEMP": tmp}
    return subprocess.run([sys.executable, __file__, "__selftest_run__"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=300)


def cmd_selftest(_):
    """在隔离目录里跑自检。系统临时目录不可写时（受限沙箱/权限异常），
    退回到数据目录所在卷上的临时目录再试一次，而不是直接失败。"""
    candidates: List[str] = []
    try:
        candidates.append(tempfile.mkdtemp(prefix="memory_selftest_"))
    except Exception:
        pass
    candidates.append(str(DATA_DIR.parent / f".memory_selftest_{os.getpid()}"))

    last = None
    for tmp in candidates:
        try:
            Path(tmp).mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        try:
            result = _selftest_subprocess(tmp)
        except Exception as e:
            last = e
            shutil.rmtree(tmp, ignore_errors=True)
            continue
        shutil.rmtree(tmp, ignore_errors=True)
        if result.returncode == 0:
            print(result.stdout.rstrip())
            return 0
        last = result
        if "PermissionError" not in (result.stderr or ""):
            print(result.stdout.rstrip())
            print(f"[自检 stderr] {result.stderr.rstrip()}")
            return result.returncode
    if isinstance(last, Exception):
        print(f"[自检执行失败] {last}")
        return 1
    if last is not None:
        print(last.stdout.rstrip())
        print(f"[自检 stderr] {last.stderr.rstrip()}")
        print("提示：自检需要在临时目录建文件，当前环境拒绝了。"
              "可先把 TMP/TEMP 指到可写目录（或给数据目录所在卷写权限）再运行。")
        return last.returncode or 1
    print("[自检执行失败] 找不到可用的临时目录")
    return 1


def build_parser():
    ap = argparse.ArgumentParser(prog="memory_onefile", description="记忆模块 · 单文件版")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    sub.add_parser("demo")
    sub.add_parser("selftest")
    sub.add_parser("__selftest_run__")

    p = sub.add_parser("zone")
    zsub = p.add_subparsers(dest="zcmd", required=True)
    zsub.add_parser("list")
    zc = zsub.add_parser("create")
    zc.add_argument("-n", "--name", required=True)
    zc.add_argument("-t", "--tags", default="")
    zc.add_argument("--note", default="")
    zd = zsub.add_parser("delete")
    zd.add_argument("zone_id")

    p = sub.add_parser("subzone")
    ssub = p.add_subparsers(dest="scmd", required=True)
    ssub.add_parser("list")
    sc = ssub.add_parser("create")
    sc.add_argument("-n", "--name", required=True)
    sc.add_argument("--zone", action="append", default=[])
    sc.add_argument("-t", "--tags", default="")
    sc.add_argument("-r", "--routes", default="")
    sc.add_argument("--active-route", default=None)
    sc.add_argument("--note", default="")
    sd = ssub.add_parser("dissolve")
    sd.add_argument("subzone_id")

    p = sub.add_parser("ingest")
    p.add_argument("--subzone", required=True)
    p.add_argument("-s", "--summary", required=True)
    p.add_argument("-c", "--content", default="")
    p.add_argument("--tags", default="")
    p.add_argument("--route", default=None)

    p = sub.add_parser("ingest-auto")
    p.add_argument("--subzone", required=True)
    p.add_argument("--segments", nargs="+", required=True)
    p.add_argument("--review", choices=["conservative", "light"], default="conservative")
    p.add_argument("--keep-original", action="store_true")
    p.add_argument("--auto-approve", action="store_true")

    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--top-k", "--top", dest="top_k", type=int, default=SEARCH_TOP_K)
    p.add_argument("--no-semantic", action="store_true")
    p.add_argument("--json", dest="json_out", action="store_true",
                   help="结构化 JSON 输出（含 premise/counter/conflict_ids/score/case_type），供 AI 接入层解析")
    p.add_argument("--rerank", dest="rerank", action="store_true", default=True)
    p.add_argument("--no-rerank", dest="rerank", action="store_false",
                   help="关闭重排（匹配度 + 可信度 + 热度）")

    p = sub.add_parser("mirror-search")
    p.add_argument("query")
    p.add_argument("--top-k", type=int, default=SEARCH_TOP_K)
    p.add_argument("--include-migrated", action="store_true")

    p = sub.add_parser("freeze")
    p.add_argument("--unit", action="append", required=True)

    p = sub.add_parser("unseal")
    p.add_argument("--unit", required=True)
    p.add_argument("--no-rebuild", action="store_true")

    sub.add_parser("migrate-check")
    sub.add_parser("lifecycle")
    sub.add_parser("decay")
    sub.add_parser("housekeeping", help="自动巡检：衰减+扫描低活跃单元生成冷封存提案（人工审批闸）")

    p = sub.add_parser("remember")
    p.add_argument("summary", nargs="+")
    p.add_argument("--detail", default="")
    p.add_argument("--tags", default="")
    p.add_argument("--premise", default="", help="适用前提：什么时候才能用这条经验")
    p.add_argument("--counter", default="", help="反面/限制/坑：什么情况下不能用")
    p.add_argument("--score", type=float, default=DEFAULT_SCORE, help="可信度 0~1")
    p.add_argument("--case-type", default=CaseType.NEUTRAL,
                   choices=[CaseType.SUCCESS, CaseType.FAIL, CaseType.NEUTRAL])
    p.add_argument("--pin", action="store_true", help="标记为重要经验（受保护，不参与自动冷封存）")
    p.add_argument("--source-ref", default="", help="溯源绑定：这条经验来自哪个对话/任务/来源")

    p = sub.add_parser("capture", help="一键收藏：显式标记 → 生成经验草稿提案 → 人工 approve 入库")
    p.add_argument("summary", nargs="+")
    p.add_argument("--detail", default="")
    p.add_argument("--tags", default="")
    p.add_argument("--premise", default="", help="适用前提")
    p.add_argument("--counter", default="", help="反面/坑")
    p.add_argument("--score", type=float, default=DEFAULT_SCORE, help="可信度 0~1")
    p.add_argument("--case-type", default=CaseType.NEUTRAL,
                   choices=[CaseType.SUCCESS, CaseType.FAIL, CaseType.NEUTRAL])
    p.add_argument("--pin", action="store_true", help="标记重要（受保护）")
    p.add_argument("--source-ref", default="", help="溯源绑定：来源对话/任务")

    p = sub.add_parser("export-dir", help="批量导出主库经验为 markdown（一条一文件，人可读可改）")
    p.add_argument("dir")

    p = sub.add_parser("import-dir", help="反向导入 markdown 目录（全部走提案 + 重复告警，不自动合并）")
    p.add_argument("dir")

    p = sub.add_parser("serve", help="开放 JSON-RPC 2.0 服务：只读方法为主，写仅 propose")
    p.add_argument("--stdio", action="store_true", help="stdio 模式：逐行读请求、逐行写响应（适合管道/嵌入）")
    p.add_argument("--http", action="store_true", help="http 模式：POST /rpc，默认仅 127.0.0.1")
    p.add_argument("--port", type=int, default=8765, help="http 端口（默认 8765）")

    p = sub.add_parser("audit", help="记忆库审计：文本摘要，或 --html 生成断网可开的静态报告")
    p.add_argument("--html", nargs="?", const="",
                   help="HTML 报告输出路径；只写 --html 不写路径则输出到 data/audit_report.html")

    p = sub.add_parser("latest")
    p.add_argument("n", nargs="?", type=int, default=5)

    p = sub.add_parser("remove")
    p.add_argument("unit_id")

    p = sub.add_parser("proposals")
    p.add_argument("--status", default=None,
                   choices=[getattr(ProposalStatus, s) for s in dir(ProposalStatus) if s.isupper()])
    p.add_argument("--prune", action="store_true",
                   help="清理已结束（executed/rejected）且超过 --days 天未变动的提案文件")
    p.add_argument("--days", type=int, default=30, help="--prune 的保留天数（默认 30）")

    p = sub.add_parser("approve")
    p.add_argument("proposal_id")
    p.add_argument("--execute", action="store_true")

    p = sub.add_parser("reject")
    p.add_argument("proposal_id")
    p.add_argument("-r", "--reason", default="人工驳回")

    p = sub.add_parser("augment", help="给已有经验补充前提/反面坑/可信度/类型/保护标记")
    p.add_argument("--id", dest="unit_id", required=True)
    p.add_argument("--premise", default=None)
    p.add_argument("--counter", default=None)
    p.add_argument("--score", type=float, default=None)
    p.add_argument("--case-type", default=None,
                   choices=[CaseType.SUCCESS, CaseType.FAIL, CaseType.NEUTRAL])
    p.add_argument("--pin", action="store_true", help="标记为重要经验（受保护）")
    p.add_argument("--unpin", action="store_true", help="解除重要经验保护")
    p.add_argument("--source-ref", default=None, help="补写/修改溯源绑定（来源对话/任务/文档）")

    p = sub.add_parser("conflict_bind", help="双向标记两条经验互相冲突")
    p.add_argument("--idA", dest="id_a", required=True)
    p.add_argument("--idB", dest="id_b", required=True)

    p = sub.add_parser("feedback", help="落地后回写效果，动态调整经验可信度")
    p.add_argument("--id", dest="unit_id", required=True)
    p.add_argument("--result", required=True, choices=["good", "bad", "neutral"])
    p.add_argument("--note", default="")

    p = sub.add_parser("export", help="导出单条经验完整 JSON（含快照数，用于溯源对照）")
    p.add_argument("--id", dest="unit_id", required=True)

    p = sub.add_parser("agent", help="先构思思路→检索经验→学习内化→独立输出（需配置 LLM）")
    p.add_argument("task", nargs="+")
    p.add_argument("--learn", type=lambda x: str(x).lower() == "true", default=True,
                   help="true=先学经验再输出（默认）；false=老模式直接检索拼接")
    p.add_argument("--top", type=int, default=6)

    return ap


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "zone":
        rc = {"list": cmd_zone_list, "create": cmd_zone_create,
              "delete": cmd_zone_delete}[args.zcmd](args)
        return rc if isinstance(rc, int) else 0
    if args.cmd == "subzone":
        rc = {"list": cmd_subzone_list, "create": cmd_subzone_create,
              "dissolve": cmd_subzone_dissolve}[args.scmd](args)
        return rc if isinstance(rc, int) else 0
    if args.cmd == "__selftest_run__":
        return _selftest_run()
    handler = {
        "init": cmd_init, "status": cmd_status, "demo": cmd_demo, "selftest": cmd_selftest,
        "ingest": cmd_ingest, "ingest-auto": cmd_ingest_auto,
        "search": cmd_search, "mirror-search": cmd_mirror_search,
        "freeze": cmd_freeze, "unseal": cmd_unseal,
        "migrate-check": cmd_migrate_check, "lifecycle": cmd_lifecycle, "decay": cmd_decay,
        "housekeeping": cmd_housekeeping,
        "remember": ops_remember_wrap, "latest": ops_latest_wrap, "remove": ops_remove_wrap,
        "capture": cmd_capture, "export-dir": cmd_export_dir, "import-dir": cmd_import_dir,
        "proposals": cmd_proposals, "approve": cmd_approve, "reject": cmd_reject,
        "augment": cmd_augment, "conflict_bind": cmd_conflict_bind,
        "feedback": cmd_feedback, "export": cmd_export, "agent": cmd_agent,
        "serve": cmd_serve, "audit": cmd_audit,
    }.get(args.cmd)
    if handler:
        rc = handler(args)
        return rc if isinstance(rc, int) else 0
    return 0


def ops_remember_wrap(a):
    ops_remember(" ".join(a.summary), detail=a.detail, tags=a.tags,
                 premise=a.premise, counter=a.counter, score=a.score,
                 case_type=a.case_type, pinned=a.pin, source_ref=a.source_ref)
    return 0


def ops_remove_wrap(a):
    return ops_remove(a.unit_id)


def ops_latest_wrap(a):
    ops_latest(a.n)
    return 0


if __name__ == "__main__":
    ensure_dirs()
    try:
        sys.exit(main())
    except MemoryModuleError as e:
        print(f"[校验失败] {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[错误] {e}")
        sys.exit(1)
