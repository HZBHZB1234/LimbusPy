"""跑通 ``python/examples/sinking_tide``：mock kernel + 真实 LPCP 通路，不需要开游戏。

    python python/examples/run_sinking_tide.py            # 完整演示
    python python/examples/run_sinking_tide.py --quiet    # 只打印结论

为什么这个文件值得存在：示例代码最容易变成"看起来对、跑不起来"。这里的驱动器是
``api_harness.ApiHarness`` —— 它用 ``MockKernel`` 扮演 C# 侧、用 ``MockOps`` 扮演 op 的
参考实现，但走的是和真机完全相同的 LPCP 帧、请求关联、SNAPSHOT、PATCH_ADD、SYNCALL 通路。
所以这里跑通，意味着 Python 侧的接线（含热重载、补丁、同步泳道）是对的；
剩下只需在真机上确认 IL2CPP 反射与 Unity 生命周期。

``python/tests/test_example_mod.py`` 复用本模块的 ``Harness``，把同样的场景跑成回归测试。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from api_harness import ApiHarness                                   # noqa: E402

MOD_NAME = "sinking_tide"
MOD_SOURCE = os.path.join(_HERE, "sinking_tide")

#: 第 3 回合：一个满血杂兵 + 一个残血且沉溺 8 层的目标 + 一个自己人
UNITS = [
    dict(instance_id=101, origin_id=1, hp=40, max_hp=40, is_player=True, position="MAIN"),
    dict(instance_id=201, origin_id=10, hp=300, max_hp=300, is_player=False, position="MAIN",
         buffs={"CHARGE": [3, 1]}),
    dict(instance_id=202, origin_id=11, hp=120, max_hp=240, is_player=False, position="SUB",
         buffs={"SINKING": [8, 2]}),
]

#: 沉溺初始层数（测试里改这个数就能验阈值逻辑）
MARKED = 202
BRUTE = 201
HERO = 101


class Harness(ApiHarness):
    """把 ``sinking_tide`` 装进一个临时 mods/ 并接到 mock kernel 上。"""

    def __init__(self, root: Optional[str] = None, frame_budget_ms: float = 200.0) -> None:
        super().__init__(MOD_NAME, MOD_SOURCE, units=UNITS, round_=3,
                         frame_budget_ms=frame_budget_ms)

    # -- 便捷读取 -----------------------------------------------------------------------
    def hp(self, instance_id: int) -> int:
        return self.unit(instance_id).hp

    def set_buff_stack(self, instance_id: int, keyword: str, stacks: int) -> None:
        """改已有 buff 的叠层（游戏里是叠上去，这里直接改值，效果等价）。"""
        entry = self.unit(instance_id).buffs.get(str(keyword).upper())
        self.set_buff(instance_id, keyword, stacks, entry[1] if entry else 1)

    def report(self) -> dict:
        return self.call("sinking_tide.report")

    @property
    def patches(self) -> Dict[str, dict]:
        return self.kernel.patches


# --------------------------------------------------------------------------------------
# 演示脚本
# --------------------------------------------------------------------------------------
def _print(title: str) -> None:
    print("\n== %s %s" % (title, "=" * max(0, 68 - len(title))))


def run(quiet: bool = False) -> int:
    harness = Harness().start()
    try:
        mod = harness.mod
        assert mod is not None, "示例 mod 没有加载: %s" % harness.runtime.load_errors

        if not quiet:
            _print("加载")
            print("mods      : %s" % sorted(harness.runtime.mods))
            print("hooks     : %s" % [(entry.timing, entry.name)
                                      for entry in harness.runtime.registry.all_hooks()])
            print("patches   : %s" % list(harness.patches))
            print("static    : %s" % mod._statics_root)
            for line in harness.logs:
                print("  log> %s" % line)

        # 1) 第一次命中：202 满血 120/240（ratio 0.5 ≤ 0.5）→ 8 层 × 3 = 24 伤害，
        #    再加上这一击本身的 20 点 → 202 应当是 120 - 24 - 20 = 76
        harness.attack(HERO, MARKED, 20)
        assert harness.hp(MARKED) == 76, "202 应当是 76，实际 %s" % harness.hp(MARKED)

        # 2) 同一轮不再爆发（once_per_round）：只扣这一击的 20
        harness.attack(HERO, MARKED, 20)
        assert harness.hp(MARKED) == 56, "同轮不应再爆发，实际 %s" % harness.hp(MARKED)

        # 3) 新一轮 + 沉溺继续叠 → 再次爆发（9 层 × 3 = 27，再扣这一击的 20）
        harness.set_buff_stack(MARKED, "SINKING", 9)
        harness.start_round()
        harness.attack(HERO, MARKED, 20)
        assert harness.hp(MARKED) == 56 - 27 - 20, "第二轮爆发后应当是 9，实际 %s" % harness.hp(MARKED)

        if not quiet:
            _print("两轮命中")
            print("  202 hp: 120 → 76 → 56 → 9（两次爆发 24 + 27）")

        # 3b) 沉溺自身造成的掉血（游戏里由 BuffModel 触发）。这类事件的 keyword 是
        #     SINKING —— ``@lp.on_damage(when=lp.ev.keyword == SINKING)`` 只收这一类，
        #     另一条 keyword 为空的普通伤害必须被过滤掉（谓词不满足时函数体不会跑）。
        harness.emit("DamageDealt", {"unit": harness.ref(MARKED), "attacker": None,
                                     "old_hp": 9, "new_hp": 6, "source": "BUFF",
                                     "keyword": "SINKING"}, victim=MARKED)
        harness.emit("DamageDealt", {"unit": harness.ref(BRUTE), "attacker": None,
                                     "old_hp": 300, "new_hp": 297, "source": "PASSIVE",
                                     "keyword": ""}, victim=BRUTE)

        # 4) 报告（暴露给内核 / REPL / 其它 mod）
        report = harness.report()
        if not quiet:
            _print("战斗报告 sinking_tide.report")
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        assert report["totals"]["bursts"] == 2, report["totals"]
        assert report["totals"]["hits"] == 3, report["totals"]
        assert report["totals"]["damage"] == 24 + 27, report["totals"]
        # 两次爆发各推了一条 DamageDealt，但它们 keyword 为空（显式伤害不是 buff 造成的），
        # 所以只有上面那条手推的 SINKING 事件被谓词收下。
        assert report["totals"]["sinking_ticks"] == 1, report["totals"]

        # 5) 同步泳道：ChangeTakeDamage 倍率（阻塞，只读内存）
        harness.mod.cfg.amplify_take_damage = True
        harness.mod.tide_armed = True
        multiplier = harness.call("sinking_tide.take_damage_multiplier",
                                  {"timing": "ChangeTakeDamage"})
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
        forced = harness.call("sinking_tide.force_burst", {"args": [BRUTE]})
        if not quiet:
            _print("手动爆发 force_burst(201)")
            print("  %s" % json.dumps(forced, ensure_ascii=False, default=str))
        assert forced["stacks"] == 6 and forced["damage"] == 18, forced
        assert harness.hp(BRUTE) == 300 - 18, harness.hp(BRUTE)

        # 8) 热重载：账本与配置必须活下来，补丁要重新装上
        totals_before = harness.report()["totals"]
        result = harness.runtime.reload([MOD_NAME], reason="example")
        assert result.ok, result.failed
        totals_after = harness.report()["totals"]
        if not quiet:
            _print("热重载")
            print("  generation : %s" % result.generation)
            print("  账本保持   : %s" % (totals_before == totals_after))
            print("  重装补丁   : %s" % list(harness.patches))
        assert totals_before == totals_after, "热重载丢了账本"
        assert len(harness.patches) == 1, "重载后补丁应当被重新装上"

        if not quiet:
            _print("统计")
            stats = harness.runtime.stats()
            print("counters  : %s" % stats["counters"])
            print("registry  : %s" % stats["registry"])

        print("\nOK: sinking_tide 在 mock kernel 上跑通"
              "（2 次爆发 / %d 伤害 / 热重载不丢账本）" % harness.report()["totals"]["damage"])
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
