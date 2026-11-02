"""跑通 ``examples/sinking_tide``：一个"游戏形状"的替身 + mock kernel，不需要开游戏。

    python LimbusPy/examples/run_sinking_tide.py            # 完整演示
    python LimbusPy/examples/run_sinking_tide.py --quiet    # 只跑断言，打印一行结论

为什么这个文件值得存在：示例代码最容易变成"看起来对、跑不起来"。这里的替身对象用的是**真实
成员名**（Hp / MaxHp / InstanceID / GetActivatedBuffModels / GetCurrentStack / ChangeHp /
AddPassive / OnChangeHp），内核侧就是 ``limbuspy._mock.MockKernel`` —— 走的是和真实游戏完全
相同的 LPCP 帧、请求关联、SNAPSHOT、PATCH_ADD、SYNCALL 通路。所以这里跑通，意味着 Python 侧
的接线（含热重载、补丁、同步泳道）是对的；剩下只需在真机上确认 IL2CPP 反射与 Unity 生命周期。

``python/tests/test_example_mod.py`` 复用本模块的替身，把同样的场景跑成回归测试。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import traceback
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.abspath(os.path.join(_HERE, "..", "python"))
if _PYTHON_ROOT not in sys.path:
    sys.path.insert(0, _PYTHON_ROOT)

from limbuspy._mock import MockKernel                      # noqa: E402
from limbuspy.mod import ModRuntime                        # noqa: E402
from limbuspy.runtime.client import Bridge                 # noqa: E402
from limbuspy.runtime.errors import BridgeError            # noqa: E402
from limbuspy.runtime.protocol import MsgType              # noqa: E402
from limbuspy.runtime.transport import FileTransport        # noqa: E402

MOD_SOURCE = os.path.join(_HERE, "sinking_tide")
MOD_NAME = "sinking_tide"


# --------------------------------------------------------------------------------------
# 一个最小但"游戏形状"的替身
# --------------------------------------------------------------------------------------
class FakeGame:
    """用真实成员名模拟 BattleUnitModel / BuffModel 的反射面。

    只实现示例 mod 真正读写的成员 —— 这不是通用模拟器，而是"这个 mod 需要什么就长什么样"，
    因此成员名写错会像真机一样直接报 notfound。
    """

    def __init__(self) -> None:
        self.handles: Dict[int, dict] = {}
        self._next = 0x1000
        self.passives: List[tuple] = []
        self.hp_calls: List[tuple] = []
        self.battle = {"round": 3, "wave": 1, "units": []}

    # -- 建模 ---------------------------------------------------------------------------
    def _alloc(self, kind: str, members: dict) -> dict:
        handle = self._next
        self._next += 1
        self.handles[handle] = {"kind": kind, "members": members}
        return {"t": kind, "h": handle}

    def add_unit(self, instance_id: int, hp: int, max_hp: int, *, player: bool = False,
                 position: int = 1) -> dict:
        members = {
            "InstanceID": instance_id,
            "Hp": hp,
            "MaxHp": max_hp,
            "Mp": 0,
            "Level": 30,
            "Position": position,
            "IsDead": lambda: members["Hp"] <= 0,
            "GetActivatedBuffModels": lambda: list(members["_buffs"]),
        }
        members["_buffs"] = []
        members["ChangeHp"] = lambda value, src, timing, attacker, action, coin, keyword=None, \
            deactive=False, ignore=False: self._change_hp(members, instance_id, value, src, timing)
        members["AddPassive"] = lambda passive_id: self._add_passive(instance_id, passive_id)
        ref = self._alloc("unit", members)
        self.battle["units"].append({
            "ref": ref, "instance_id": instance_id, "origin_id": instance_id,
            "hp": hp, "max_hp": max_hp, "mp": 0, "level": 30, "position": position,
            "is_dead": hp <= 0, "is_player": player,
            "faction": "PLAYER" if player else "ENEMY",
        })
        return ref

    def add_buff(self, unit_ref: dict, keyword: str, stack: int, turn: int = 1) -> dict:
        members = {
            "GetMainKeyword": keyword,
            "GetCurrentStack": stack,
            "GetCurrentTurn": turn,
            "GetMaxStack": 99,
            "GetMaxTurn": 99,
            "IsKeyword": lambda wanted, _kw=keyword: str(wanted).upper() == _kw.upper(),
        }
        ref = self._alloc("buff", members)
        self.handles[unit_ref["h"]]["members"]["_buffs"].append(ref)
        return ref

    def set_buff_stack(self, unit_ref: dict, keyword: str, stack: int) -> None:
        """改已有 buff 的叠层（游戏里是叠上去，这里直接改值，效果等价）。"""
        for ref in self.handles[unit_ref["h"]]["members"]["_buffs"]:
            members = self.handles[ref["h"]]["members"]
            if str(members["GetMainKeyword"]).upper() == keyword.upper():
                members["GetCurrentStack"] = int(stack)
                return
        self.add_buff(unit_ref, keyword, stack)

    # -- 被 mod 调用的游戏方法 ----------------------------------------------------------
    def _change_hp(self, members, instance_id, value, src, timing) -> int:
        """``BattleUnitModel.ChangeHp``：value 是**目标 HP 值**，返回真实变化量。"""
        old = int(members["Hp"])
        new = max(0, min(int(value), int(members["MaxHp"])))
        members["Hp"] = new
        self.hp_calls.append((instance_id, old, new, src, timing))
        for entry in self.battle["units"]:
            if entry["instance_id"] == instance_id:
                entry["hp"] = new
                entry["is_dead"] = new <= 0
        return new - old

    def _add_passive(self, instance_id, passive_id) -> None:
        self.passives.append((instance_id, passive_id))

    # -- 反射面 -------------------------------------------------------------------------
    def get(self, target, member) -> Any:
        entry = self._handles(target)
        if member not in entry["members"]:
            raise BridgeError("notfound", "%s has no member %r" % (entry["kind"], member))
        value = entry["members"][member]
        if callable(value):
            try:
                return value()
            except TypeError:
                return value
        return value

    def invoke(self, target, member, args) -> Any:
        entry = self._handles(target)
        if member not in entry["members"]:
            raise BridgeError("notfound", "%s has no member %r" % (entry["kind"], member))
        value = entry["members"][member]
        if not callable(value):
            return value
        return value(*list(args or []))

    def snapshot(self) -> dict:
        battle = dict(self.battle)
        battle["units"] = [dict(entry) for entry in self.battle["units"]]
        return battle

    def _handles(self, target) -> dict:
        if not isinstance(target, dict) or "h" not in target:
            raise BridgeError("notfound", "unsupported target %r" % (target,))
        entry = self.handles.get(int(target["h"]))
        if entry is None:
            raise BridgeError("stale_handle", "handle 0x%x is gone" % int(target["h"]))
        return entry

    def handlers(self) -> Dict[str, Any]:
        return {
            "reflect.get": lambda target, member: self.get(target, member),
            "reflect.invoke": lambda target, member, args: self.invoke(target, member, args),
            # 没有 "reflect.types" 处理器 → Modular.available 判为 False，走优雅降级分支
        }


# --------------------------------------------------------------------------------------
# 装配：临时 mods/ + mock kernel + ModRuntime
# --------------------------------------------------------------------------------------
class Harness:
    def __init__(self, root: Optional[str] = None, frame_budget_ms: float = 200.0) -> None:
        self.root = root or tempfile.mkdtemp(prefix="limbuspy-example-")
        self._owns_root = root is None
        self.mods_dir = os.path.join(self.root, "mods")
        self.state_dir = os.path.join(self.root, "state")
        os.makedirs(self.mods_dir, exist_ok=True)
        os.makedirs(self.state_dir, exist_ok=True)

        destination = os.path.join(self.mods_dir, MOD_NAME)
        if not os.path.isdir(destination):
            shutil.copytree(MOD_SOURCE, destination,
                            ignore=shutil.ignore_patterns("__pycache__"))

        self.game = FakeGame()
        self.logs: List[str] = []

        client_file, kernel_file = _pipe_pair()
        self.kernel = MockKernel(
            FileTransport(kernel_file, "mock-kernel"),
            handlers=self.game.handlers(),
            battle=self.game.snapshot(),
        )
        # SNAPSHOT 必须每次现算：爆发扣血之后再读战场，值要变。
        self.kernel.bridge.on_request(MsgType.SNAPSHOT, lambda payload: self.game.snapshot())

        self.bridge = Bridge(FileTransport(client_file, "limbuspy"), name="example").start()
        self.runtime = ModRuntime(self.bridge, self.mods_dir, self.state_dir,
                                  logger=self.logs.append, frame_budget_ms=frame_budget_ms)
        self._thread = threading.Thread(target=lambda: self.kernel.start(5.0), daemon=True)

    # -- 生命周期 -----------------------------------------------------------------------
    def start(self) -> "Harness":
        self._thread.start()
        self.runtime.start()
        self._thread.join(5.0)
        return self

    def close(self) -> None:
        try:
            self.runtime.stop()
        finally:
            self.bridge.close()
            self.kernel.close()
            if self._owns_root:
                shutil.rmtree(self.root, ignore_errors=True)

    # -- 驱动 ---------------------------------------------------------------------------
    def emit(self, events: List[dict]) -> List[dict]:
        return self.kernel.emit_event_batch(events)

    def call(self, fn: str, ctx: Optional[dict] = None) -> Any:
        return self.kernel.sync_call(fn, ctx or {})

    def report(self) -> dict:
        return self.call("sinking_tide.report")

    @property
    def mod(self):
        loaded = self.runtime.mods.get(MOD_NAME)
        return loaded.instance if loaded is not None else None


def _pipe_pair():
    """一对相连的字节流（Windows 上 socketpair 也有，等同于真实命名管道的行为）。"""
    import socket

    left, right = socket.socketpair()
    left_file = left.makefile("rwb", buffering=0)
    right_file = right.makefile("rwb", buffering=0)
    left.close()
    right.close()
    return left_file, right_file


# --------------------------------------------------------------------------------------
# 演示脚本
# --------------------------------------------------------------------------------------
def _print(title: str) -> None:
    print("\n== %s %s" % (title, "=" * max(0, 68 - len(title))))


def build_scenario(game: FakeGame) -> Dict[str, Any]:
    """第 3 轮：一个满血杂兵 + 一个残血且沉溺 8 层的目标。"""
    hero = game.add_unit(101, 40, 40, player=True, position=1)
    brute = game.add_unit(201, 300, 300, position=4)
    marked = game.add_unit(202, 120, 240, position=5)
    game.add_buff(marked, "Sinking", 8, turn=2)
    game.add_buff(brute, "Charge", 3, turn=1)
    return {"hero": hero, "brute": brute, "marked": marked}


def run(quiet: bool = False) -> int:
    harness = Harness().start()
    try:
        refs = build_scenario(harness.game)
        mod = harness.mod
        assert mod is not None, "示例 mod 没有加载: %s" % harness.runtime.load_errors

        if not quiet:
            _print("加载")
            print("mods      : %s" % sorted(harness.runtime.mods))
            print("hooks     : %s" % [(entry.timing, entry.name)
                                      for entry in harness.runtime.registry.all_hooks()])
            print("patches   : %s" % list(harness.kernel.patches))
            print("static    : %s" % mod._statics_root)
            for line in harness.logs:
                print("  log> %s" % line)

        # 1) 命中（头部）→ 记账；沉溺 8 层 ≥ 阈值 6，且 hp_ratio 0.5 ≤ 0.5 → 触发爆发
        events = [
            {"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": True, "crit": False},
            {"timing": "OnGainBuff", "ctx": refs["marked"], "keyword": "SINKING"},
            {"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": False, "crit": True},
            {"timing": "OnSucceedAttack", "ctx": refs["brute"], "head": True, "crit": False},
        ]
        actions = harness.emit(events)

        if not quiet:
            _print("第一波命中（4 个事件，1 次 EVBATCH 往返）")
            for index, action in enumerate(actions):
                print("  event %d → effects %s" % (action.get("event"), action.get("effects")))

        live = harness.game.battle["units"]
        marked_hp = [entry["hp"] for entry in live if entry["instance_id"] == 202][0]
        assert marked_hp == 120 - 8 * 3, "202 应当被打到 96，实际 %s" % marked_hp
        assert [call[0] for call in harness.game.hp_calls] == [202]

        # 2) 同轮不再爆发（once_per_round），叠层降到阈值以下也不触发
        harness.emit([{"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": True}])

        # 3) 新一轮 + 沉溺继续叠 → 再次爆发
        harness.game.battle["round"] = 4
        harness.game.set_buff_stack(refs["marked"], "Sinking", 9)
        harness.emit([{"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": True}])
        marked_hp = [entry["hp"] for entry in harness.game.battle["units"]
                     if entry["instance_id"] == 202][0]
        assert marked_hp == 96 - 9 * 3, "第二轮爆发后应当是 69，实际 %s" % marked_hp

        # 4) 报告（暴露给内核 / REPL / 其它 mod）
        report = harness.report()
        if not quiet:
            _print("战斗报告 sinking_tide.report")
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        assert report["totals"]["bursts"] == 2
        assert report["totals"]["hits"] == 5
        assert report["totals"]["damage"] == 24 + 27
        assert report["totals"]["sinking_gained"] == 1

        # 5) 同步泳道：ChangeTakeDamage 倍率（阻塞，只读内存）
        harness.mod.cfg.amplify_take_damage = True
        harness.mod.tide_armed = True
        multiplier = harness.call("sinking_tide.take_damage_multiplier", {"timing": "ChangeTakeDamage"})
        assert multiplier == 115, multiplier
        if not quiet:
            _print("同步钩子 ChangeTakeDamage")
            print("  返回 %d（100 = 不变；这个值会直接替换游戏的伤害数字）" % multiplier)

        # 6) Harmony 补丁：模拟内核在 OnChangeHp 上派发一次 prefix
        before = harness.mod.ledger.hp_events
        harness.call("sinking_tide.on_hp_changed",
                     {"patch": "BattleUnitModel.OnChangeHp#1", "kind": "prefix",
                      "args": [120, 96, "PASSIVE", "ON_SUCCESS_ATTACK", None, None, 2123]})
        assert harness.mod.ledger.hp_events == before + 1
        if not quiet:
            _print("Harmony 补丁 OnChangeHp（prefix，观测）")
            print("  最近一条: %s" % (list(harness.mod.hp_events)[-1],))

        # 7) 手动触发（REPL / 宏用）：201 身上没有沉溺 → 用阈值兜底
        forced = harness.call("sinking_tide.force_burst", {"args": [201]})
        if not quiet:
            _print("手动爆发 force_burst(201)")
            print("  %s" % json.dumps(forced, ensure_ascii=False, default=str))
        assert forced["stacks"] == 6 and forced["damage"] == 18, forced
        assert forced["hp"] == [300, 282], forced

        # 8) 热重载：账本与配置必须活下来
        totals_before = harness.report()["totals"]
        result = harness.runtime.reload([MOD_NAME], reason="example")
        assert result.ok, result.failed
        totals_after = harness.report()["totals"]
        if not quiet:
            _print("热重载")
            print("  generation : %s" % result.generation)
            print("  账本保持   : %s" % (totals_before == totals_after))
            print("  重装补丁   : %s" % list(harness.kernel.patches))
        assert totals_before == totals_after, "热重载丢了账本"

        if not quiet:
            _print("统计")
            stats = harness.runtime.stats()
            print("counters  : %s" % stats["counters"])
            print("registry  : %s" % stats["registry"])

        print("\nOK: sinking_tide 在 mock kernel 上跑通（3 次爆发 / %d 伤害 / 热重载不丢账本）"
              % harness.report()["totals"]["damage"])
        return 0
    finally:
        harness.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="在无游戏环境下跑通 sinking_tide 示例")
    parser.add_argument("--quiet", action="store_true", help="只打印最终结论")
    args = parser.parse_args(argv)
    try:
        return run(quiet=args.quiet)
    except AssertionError as exc:
        print("\nFAILED: %s" % exc)
        return 1
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
