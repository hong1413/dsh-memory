# -*- coding: utf-8 -*-
"""星图数据导出（只读）：把记忆库结构一次性输出成 JSON，供前端星图渲染。

由 dsh-memory 插件的 Host 端调用（HTTP 路由 /dsh-memory/starmap.json），
复用同一份 memory_onefile.py 与同一套数据目录，不写入任何内容。

用法：
    python starmap_dump.py
环境变量：
    MEMORY_DATA_DIR  数据目录（不设则按 memory_onefile 的解析规则）
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import memory_onefile as m  # noqa: E402


def _unit_payload(u, extra=None):
    d = {
        "id": u.unit_id,
        "subzone_id": u.subzone_id,
        "summary": u.summary,
        "premise": u.premise or "",
        "counter": u.counter or "",
        "score": round(float(u.score), 3),
        "case_type": u.case_type,
        "tags": list(u.tags or []),
        "pinned": bool(u.pinned),
        "maturity": u.maturity,
        "timestamp": u.timestamp,
        "heat": round(float(u.active_score), 2),
        "conflicts": list(u.conflict_ids or []),
        "source_ref": u.source_ref or "",
        "source_layer": u.source_layer,
        "version": u.version,
    }
    if extra:
        d.update(extra)
    return d


def main():
    m.ensure_dirs()
    zones = [
        {"id": z.zone_id, "name": z.name, "tags": list(z.global_tags or []), "note": z.note or ""}
        for z in m.load_zones()
    ]
    subzones = [
        {
            "id": s.subzone_id,
            "name": s.name,
            "zone_ids": list(s.zone_ids or []),
            "tags": list(s.tags or []),
            "status": s.status,
        }
        for s in m.load_subzones()
    ]
    units = [_unit_payload(u) for u in m.list_units()]
    cold = [
        _unit_payload(c.unit, {"frozen_at": c.frozen_at, "mark": c.migration_mark})
        for c in m.list_cold_units()
    ]
    mirrors = [
        {
            "subzone_id": mm.subzone_id,
            "name": mm.name,
            "zone_ids": list(mm.zone_ids or []),
            "tags": list(mm.tags or []),
            "cold_unit_ids": list(mm.cold_unit_ids or []),
        }
        for mm in m.load_mirror_subzones()
    ]
    pending = list(m.list_proposals(m.ProposalStatus.PENDING))

    print(json.dumps(
        {
            "ok": True,
            "generatedAt": m._iso_now(),
            "dataDir": str(m.DATA_DIR),
            "zones": zones,
            "subzones": subzones,
            "units": units,
            "cold": cold,
            "mirrors": mirrors,
            "proposals": [
                {
                    "id": p.proposal_id,
                    "kind": p.kind,
                    "plugin": p.plugin,
                    "reason": p.reason or "",
                    "created_at": p.created_at,
                    "payload": p.payload,
                }
                for p in pending
            ],
            "stats": {
                "zones": len(zones),
                "subzones": len([s for s in subzones if s["status"] == m.SubzoneStatus.ACTIVE]),
                "units": len(units),
                "cold": len(cold),
                "pending": len(pending),
                "pinned": len([u for u in units if u["pinned"]]),
            },
        },
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
