"""示例的公共驱动器（不需要游戏、不需要 C#）。

它做四件事：

1. 造一个「游戏形状」的战斗（``MockBattle``，状态与句柄都是真的）；
2. 用 ``MockKernel`` + 真实 LPCP 通路把 mod 拉起来（和真机同一条代码路径）；
3. **扮演 C# 侧补上 payload 里那两个字段**：``mask_victim`` / ``mask_self``（按 mod 发上来的
   ``content.plan`` 算，和 ``MaskPlan.Mask`` 同语义）以及 ``round`` —— 于是
   ``lp.victim.has(Buff)`` 这类谓词在无游戏环境下也是**真的**在跑同一套逻辑；
4. 提供 ``attack`` / ``emit`` / ``start_round`` 这类驱动动作。

示例只需要关心「调用门面时发生什么」，不用关心管道、帧、句柄。
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, ".."))  # python/examples → python/
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from limbuspy._mock import MockBattle, MockKernel, MockOps, MockUnit          # noqa: E402
from limbuspy.game.session import Session                                     # noqa: E402
from limbuspy.mod.runtime import ModRuntime                                   # noqa: E402
from limbuspy.runtime.client import Bridge                                    # noqa: E402
from limbuspy.runtime.protocol import MsgType                                 # noqa: E402
from limbuspy.runtime.transport import FileTransport                          # noqa: E402

DEFAULT_UNITS = [
    # instance_id, hp, max_hp, is_player, position, buffs
    dict(instance_id=101, origin_id=1, hp=40, max_hp=40, is_player=True, position="MAIN"),
    dict(instance_id=102, origin_id=2, hp=30, max_hp=70, is_player=True, position="SUB",
         buffs={"CHARGE": [2, 2]}),
    dict(instance_id=201, origin_id=10, hp=300, max_hp=300, is_player=False, position="MAIN",
         buffs={"SINKING": [8, 3]}),
    dict(instance_id=202, origin_id=11, hp=120, max_hp=240, is_player=False, position="SUB",
         buffs={"SINKING": [2, 1]}),
]


def pipe_pair():
    left, right = socket.socketpair()
    left_file = left.makefile("rwb", buffering=0)
    right_file = right.makefile("rwb", buffering=0)
    left.close()
    right.close()
    return left_file, right_file


class ApiHarness:
    """把一个示例 mod 跑起来，并驱动它。"""

    def __init__(self, mod_name: str, mod_source: str, units: Optional[List[dict]] = None,
                 round_: Optional[int] = 1, frame_budget_ms: float = 200.0) -> None:
        self.mod_name = mod_name
        self.root = tempfile.mkdtemp(prefix="limbuspy-a-")
        self.mods_dir = os.path.join(self.root, "mods")
        self.state_dir = os.path.join(self.root, "state")
        os.makedirs(self.mods_dir, exist_ok=True)
        os.makedirs(self.state_dir, exist_ok=True)

        import copy
        import shutil
        shutil.copytree(mod_source, os.path.join(self.mods_dir, mod_name),
                        ignore=shutil.ignore_patterns("__pycache__"))

        # 必须深拷贝：``DEFAULT_UNITS`` 是模块级常量，浅拷会让两个 harness 共用同一个
        # ``buffs`` 字典 —— 上一个示例把叠层打完，下一个示例开局就是 0 层。
        self.battle = MockBattle(
            [MockUnit(**copy.deepcopy(data)) for data in (units or DEFAULT_UNITS)],
            round_=round_)
        self.ops = MockOps(self.battle)
        self.logs: List[str] = []

        client_file, kernel_file = pipe_pair()
        self.kernel = MockKernel(
            FileTransport(kernel_file, "mock-kernel"),
            handlers=self._reflect_handlers(),
            ops=self.ops,
            battle=self.snapshot(),
        )
        self.kernel.bridge.on_request(MsgType.SNAPSHOT, lambda payload: self.snapshot())
        self.bridge = Bridge(FileTransport(client_file, "limbuspy"), name="a-layer").start()
        self.runtime = ModRuntime(self.bridge, self.mods_dir, self.state_dir,
                                  logger=self.logs.append, frame_budget_ms=frame_budget_ms)
        self._thread = threading.Thread(target=lambda: self.kernel.start(5.0), daemon=True)
        self.session: Optional[Session] = None

    # -- 生命周期 -----------------------------------------------------------------------

    def start(self) -> "ApiHarness":
        self._thread.start()
        self.runtime.start()
        self._thread.join(5.0)
        self.session = Session(self.bridge)
        return self

    def close(self) -> None:
        import shutil
        try:
            self.runtime.stop()
        finally:
            self.bridge.close()
            self.kernel.close()
            shutil.rmtree(self.root, ignore_errors=True)

    # -- 游戏世界 -----------------------------------------------------------------------

    def unit(self, instance_id: int) -> MockUnit:
        for candidate in self.battle.units:
            if candidate.instance_id == instance_id:
                return candidate
        raise KeyError(instance_id)

    def ref(self, instance_id: int) -> Dict[str, Any]:
        return self.battle.ref(self.unit(instance_id))

    def hp(self, instance_id: int) -> int:
        """某个单位的当前血量（直接读模拟状态，不产生任何往返）。"""
        return int(self.unit(instance_id).hp)

    def shield(self, instance_id: int) -> int:
        return int(self.unit(instance_id).shield)

    def buff_stacks(self, instance_id: int, keyword: str) -> int:
        entry = self.unit(instance_id).buffs.get(str(keyword).upper())
        return int(entry[0]) if entry else 0

    def set_buff(self, instance_id: int, keyword: str, stacks: int, turns: int = 1) -> None:
        """给单位挂/改一个 Buff（模拟游戏里已经挂上去的状态）。"""
        self.unit(instance_id).buffs[str(keyword).upper()] = [
            int(stacks), int(turns), 99, 99]

    def snapshot(self) -> Dict[str, Any]:
        return {
            "round": self.battle.round,
            "wave": 1,
            "units": [
                {"ref": self.battle.ref(unit), "instance_id": unit.instance_id,
                 "hp": unit.hp, "max_hp": unit.max_hp, "is_dead": unit.is_dead,
                 "is_player": unit.is_player}
                for unit in self.battle.units
            ],
        }

    # -- 扮演 C# 侧：位图与回合 ----------------------------------------------------------

    def mask(self, timing: str, instance_id: Optional[int], role: str = "victim") -> int:
        """按 mod 发上来的 ``content.plan`` 算第 ``role`` 个角色的 buff 位图。

        真机上这一步是 C# 的 ``MaskPlan.Mask`` 在补丁回调里做的（所以 Python 侧 0 往返）；
        这里用同一份计划复算一遍，好让示例在无游戏环境下也走**同一套判定**。
        """
        if instance_id is None:
            return 0
        unit = self.unit(instance_id)
        return int(self.ops.mask_for(timing, unit, role))

    def emit(self, timing: str, payload: Optional[Dict[str, Any]] = None,
             victim: Optional[int] = None, self_unit: Optional[int] = None,
             **extra: Any) -> List[dict]:
        """推一个时点事件（一次 EVENTBATCH 往返），并把位图/回合补进 payload。"""
        data: Dict[str, Any] = dict(payload or {})
        data.update(extra)
        data.setdefault("timing", timing)
        data.setdefault("round", self.battle.round)
        if victim is not None:
            data["mask_victim"] = self.mask(timing, victim, "victim")
        if self_unit is not None:
            data["mask_self"] = self.mask(timing, self_unit, "self")
        return self.kernel.emit_event_batch([{"timing": timing, "payload": data}])

    # -- 驱动 ---------------------------------------------------------------------------

    def flush(self) -> List[dict]:
        """把 op 触发的事件推给 mod（等价内核的帧末 EVENTBATCH）。"""
        events = self.ops.drain_events()
        if events:
            self.kernel.emit_event_batch(events)
        return events

    def attack(self, attacker_id: int, target_id: int, damage: int,
               is_critical: bool = False) -> None:
        """一次攻击：先推 AttackSucceeded，再走 A 层的 damage op（会再推 DamageDealt）。"""
        self.emit("AttackSucceeded", {
            "unit": self.ref(attacker_id),
            "target": self.ref(target_id),
            "final_damage": damage,
            "real_damage": damage,
            "hp_damage": damage,
            "is_critical": is_critical,
            "timing": "ON_SUCCESS_ATTACK",
        }, victim=target_id, self_unit=attacker_id)
        self.ops.dispatch("unit.damage", {
            "unit": self.ref(target_id), "amount": damage,
            "attacker": self.ref(attacker_id),
            "source": "SKILL", "timing": "ON_SUCCESS_ATTACK",
        })
        self.flush()

    def start_round(self, round_: Optional[int] = None) -> List[dict]:
        """进入新回合：更新战场回合数 + 推 ``RoundStarted``（运行时靠它数回合）。"""
        if round_ is not None:
            self.battle.round = int(round_)
        elif self.battle.round is not None:
            self.battle.round = int(self.battle.round) + 1
        return self.emit("RoundStarted", {"unit": None})

    def start_turn(self) -> None:
        """所有活着的单位依次进入「回合开始」。"""
        for unit in self.battle.units:
            if unit.is_dead:
                continue
            self.emit("TurnStarted", {
                "unit": self.battle.ref(unit), "timing": "ON_START_ROUND",
            }, self_unit=unit.instance_id)

    def call(self, fn: str, ctx: Optional[dict] = None) -> Any:
        """按名调用 mod 的 ``@expose`` 函数（内核 SYNCALL）。"""
        return self.kernel.sync_call(fn, ctx or {})

    # -- 计数 ---------------------------------------------------------------------------

    @property
    def op_calls(self) -> int:
        """A 层 op 调用次数（= 往返次数）。"""
        return self.bridge.counters.op_calls

    @property
    def reflect_calls(self) -> int:
        """B 层反射调用次数（示例里应当始终为 0）。"""
        return self.bridge.counters.calls

    # -- mod ----------------------------------------------------------------------------

    @property
    def mod(self):
        loaded = self.runtime.mods.get(self.mod_name)
        return loaded.instance if loaded is not None else None

    def _reflect_handlers(self) -> Dict[str, Any]:
        """B 层（反射）用的最小实现：让「高级层对照」脚本也能跑。"""
        def resolve(ref, member):
            unit = self.battle.resolve(ref)
            mapping = {
                "Hp": lambda: unit.hp, "MaxHp": lambda: unit.max_hp, "Mp": lambda: unit.mp,
                "InstanceID": lambda: unit.instance_id, "IsDead": lambda: unit.is_dead,
                "IsBreak": lambda: unit.is_break,
            }
            if member not in mapping:
                raise KeyError("no member %r" % (member,))
            return mapping[member]()

        def invoke(ref, member, args):
            unit = self.battle.resolve(ref)
            if member == "GetActivatedBuffStack":
                keyword = str(args[0] if args else "").upper()
                entry = unit.buffs.get(keyword)
                return int(entry[0]) if entry else 0
            if member == "AddPassive":
                passive_id = int(args[0] if args else 0)
                if passive_id not in unit.passives:
                    unit.passives.append(passive_id)
                return None
            raise KeyError("no method %r" % (member,))

        return {"reflect.get": resolve, "reflect.invoke": invoke}
