#!/usr/bin/env python
"""类型化 API 的可运行演示 / 冒烟测试（不需要游戏，不需要 C#）。

它同时是 ``limbuspy.api.bindings`` 的**契约测试原型**：

* 生成的绑定类在真实 LPCP 通路上读写（mock kernel + 真实 transport）；
* 统计每次操作的**往返次数**，把"一次属性 = 一次往返"的成本显式化；
* 打印真实 docstring，证明"每个方法都有文档"不是口号。

    python tools/demo_typed_api.py
"""

from __future__ import annotations

import inspect
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "python"))

from limbuspy._mock import MockKernel                      # noqa: E402
from limbuspy.api import bindings as game                  # noqa: E402
from limbuspy.runtime.client import Bridge                 # noqa: E402
from limbuspy.runtime.transport import FileTransport       # noqa: E402

import socket                                              # noqa: E402

# --------------------------------------------------------------------------------------
# 一个"游戏形状"的替身：成员名与真实 dump 一致，所以生成的绑定类能直接压上来
# --------------------------------------------------------------------------------------

MODEL = {}
NEXT = [1000]


def add(kind, members):
    handle = NEXT[0]
    NEXT[0] += 1
    MODEL[handle] = {"kind": kind, "members": dict(members)}
    return {"t": kind, "h": handle}


def build_model():
    MODEL.clear()
    sinking = add("buff", {
        "GetCurrentStack": 3, "GetMaxStack": 99, "GetCurrentTurn": 2, "GetMaxTurn": 99,
        "GetMainKeyword": "SINKING", "GetName": "Sinking", "BuffType": "Negative",
        "IsKeyword": lambda keyword: str(keyword).upper() == "SINKING",
    })
    hero = add("unit", {
        "Hp": 40, "MaxHp": 40, "Mp": 3, "Level": 30, "Position": 1, "InstanceID": 101,
        "GetOriginUnitID": 1001, "IsDead": False, "GetOpponentFaction": "ENEMY",
        "GetActivatedBuffModels": [sinking], "HpRetentionRatio": 0.5,
    })
    enemy = add("unit", {
        "Hp": 300, "MaxHp": 300, "Mp": 0, "Level": 30, "Position": 2, "InstanceID": 201,
        "GetOriginUnitID": 2001, "IsDead": False, "GetOpponentFaction": "PLAYER",
        "GetActivatedBuffModels": [], "HpRetentionRatio": 1.0,
    })
    return hero, enemy, sinking


def resolve(target, member):
    entry = MODEL.get(int(target.get("h") or 0)) if isinstance(target, dict) else None
    if entry is None:
        raise KeyError("stale ref %r" % (target,))
    if member not in entry["members"]:
        raise KeyError("%s has no member %r" % (entry["kind"], member))
    value = entry["members"][member]
    return value() if callable(value) else value


def assign(target, member, value):
    entry = MODEL.get(int(target.get("h") or 0)) if isinstance(target, dict) else None
    if entry is None:
        raise KeyError("stale ref")
    entry["members"][member] = value
    return value


def invoke(target, member, args):
    value = resolve(target, member)
    return value(*args) if callable(value) else value


# --------------------------------------------------------------------------------------
# 往返计数 + 装配
# --------------------------------------------------------------------------------------


class CountingBridge:
    def __init__(self, bridge):
        self._bridge = bridge
        self.trips = 0

    def request(self, *args, **kwargs):
        self.trips += 1
        return self._bridge.request(*args, **kwargs)

    def call(self, *args, **kwargs):
        self.trips += 1
        return self._bridge.call(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._bridge, name)


def connect():
    left, right = socket.socketpair()
    kernel = MockKernel(
        FileTransport(right.makefile("rwb", buffering=0), "kernel"),
        handlers={
            "reflect.get": resolve,
            "reflect.set": assign,
            "reflect.invoke": invoke,
        },
        battle={"round": 3, "wave": 1, "units": []},
    )
    bridge = Bridge(FileTransport(left.makefile("rwb", buffering=0), "client"), name="demo").start()
    thread = threading.Thread(target=lambda: kernel.start(5.0), daemon=True)
    thread.start()
    bridge.handshake(mods=[], stubs_build="")
    counting = CountingBridge(bridge)
    from limbuspy.game.session import Session

    return kernel, bridge, counting, Session(counting)


# --------------------------------------------------------------------------------------
# 演示
# --------------------------------------------------------------------------------------


def main() -> int:
    hero_ref, enemy_ref, buff_ref = build_model()
    kernel, bridge, counting, session = connect()

    rows = []

    def check(label, actual, expected):
        ok = actual == expected
        rows.append((label, repr(actual), repr(expected), "ok" if ok else "FAIL"))
        return ok

    try:
        hero = game.BattleUnitModel(session, hero_ref)

        counting.trips = 0
        ok = check("unit.hp", hero.hp, 40)
        ok &= check("一次属性读取的往返", counting.trips, 1)

        counting.trips = 0
        ok &= check("unit.max_hp", hero.max_hp, 40)
        ok &= check("三次属性读取的往返", (counting.trips, hero.mp, counting.trips), (1, 3, 2))

        ok &= check("unit.is_dead()", hero.is_dead(), False)
        ok &= check("unit.instance_id", hero.instance_id, 101)
        ok &= check("unit.position（枚举→IntEnum）", hero.position.name, "MAIN")

        counting.trips = 0
        buffs = hero.get_activated_buff_models()
        ok &= check("引用列表被包装成 BuffModel", [type(b).__name__ for b in buffs], ["BuffModel"])
        ok &= check("buff.get_current_stack()", buffs[0].get_current_stack(), 3)
        ok &= check("buff.get_main_keyword()", buffs[0].get_main_keyword(), "SINKING")

        # 写属性（HpRetentionRatio 在索引里 get+set）
        hero.hp_retention_ratio = 0.25
        ok &= check("写属性落到 _write", resolve(hero_ref, "HpRetentionRatio"), 0.25)

        # 重载方法（ChangeHp 有默认值，从 dump.cs 抓来的 2123 等）
        signature = str(inspect.signature(game.BattleUnitModel.change_hp))
        ok &= check("ChangeHp 的默认值来自 dump.cs", "=2123" in signature.replace(" ", ""), True)

        # 显式逃生口
        ok &= check("raw.get", hero.raw.get("Hp"), 40)

        # 批量读：2 个单位 × 3 个字段
        session.refresh()  # 清缓存，让 select() 的往返次数可观测
        counting.trips = 0
        table = game.BattleUnitModel.select(
            session, [hero, game.BattleUnitModel(session, enemy_ref)], "Hp", "MaxHp", "InstanceID")
        ok &= check("select() 行数", len(table), 2)
        ok &= check("select() 内容", (table[0]["Hp"], table[1]["MaxHp"]), (40, 300))
        expected = 1 if session.bridge.has_cap("batch.v1") else 6
        ok &= check("select() 往返次数（batch.v1 → 1 次；否则 6 次）", counting.trips, expected)

        print("%-44s %-28s %-22s %s" % ("操作", "实际", "期望", ""))
        print("-" * 108)
        for label, actual, expected, status in rows:
            print("%-44s %-28s %-22s %s" % (label, actual, expected, status))

        print()
        print("docstring 抽样")
        print("-" * 108)
        print("[BattleUnitModel.hp]\n%s" % inspect.getdoc(game.BattleUnitModel.hp))
        print()
        print("[BattleUnitModel.change_hp 的第一段]\n%s"
              % (inspect.getdoc(game.BattleUnitModel.change_hp) or "").split("\n\n")[0])
        print()
        print("签名: %s" % signature)
        return 0 if all(row[3] == "ok" for row in rows) else 1
    finally:
        bridge.close("demo done")
        kernel.close()


if __name__ == "__main__":
    sys.exit(main())
