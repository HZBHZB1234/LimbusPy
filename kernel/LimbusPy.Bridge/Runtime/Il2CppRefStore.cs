using System;
using Il2CppInterop.Runtime.InteropTypes;
using LimbusPy.Bridge.Core.Bridge;
using LimbusPy.Bridge.Core.Protocol;
using LimbusPy.Bridge.Core.Reflect;

namespace LimbusPy.Bridge.Runtime
{
    /// <summary>
    /// Stores IL2CPP objects behind u32 handles.
    ///
    /// Liveness: <see cref="Il2CppObjectBase.WasCollected"/> plus a non-zero native pointer, so a
    /// handle whose object was collected by the IL2CPP GC is reported as <c>stale_handle</c>
    /// instead of crashing on a dereference.
    ///
    /// Kind: inferred from the interop type name. A wrong kind is cosmetic (the wire type only
    /// helps Python pick a wrapper class); the real type check happens in <see cref="CallMarshaller"/>.
    /// </summary>
    internal sealed class Il2CppRefStore : IRefStore
    {
        private readonly HandleTable _table;

        public Il2CppRefStore(HandleTable table)
        {
            _table = table ?? throw new ArgumentNullException(nameof(table));
        }

        public HandleTable Table => _table;

        public bool TryResolve(uint handle, out object target)
        {
            if (_table.TryResolve(handle, out _, out target)) return true;
            target = null;
            return false;
        }

        public uint Store(object target)
        {
            if (target == null) return 0;
            return _table.Alloc(ClassifyOf(target), target);
        }

        public string KindOf(object target) => HandleKindNames.ToWire(ClassifyOf(target));

        public bool TryDescribe(uint handle, out HandleEntryInfo info) => _table.TryDescribe(handle, out info);

        public bool Pin(uint handle, TimeSpan? ttl = null) => _table.Pin(handle, ttl);

        public bool Release(uint handle) => _table.Release(handle);

        public int Sweep() => _table.Sweep();

        public HandleStats Stats => _table.Stats;

        // -- classification ----------------------------------------------------------------

        private static HandleKind ClassifyOf(object target)
        {
            if (target == null) return HandleKind.None;
            var name = target.GetType().Name;
            switch (name)
            {
                case "BattleUnitModel":
                case "BattleUnitModel_Abnormality":
                case "BattleUnitModel_Abnormality_Part":
                    return HandleKind.Unit;
                case "SkillModel":
                    return HandleKind.Skill;
                case "CoinModel":
                    return HandleKind.Coin;
                case "BuffModel":
                    return HandleKind.Buff;
                case "BattleActionModel":
                    return HandleKind.Action;
                case "StageModel":
                case "StageController":
                    return HandleKind.Stage;
                case "PassiveModel":
                case "EgoPassiveModel":
                case "SkillAbility":
                case "CoinAbility":
                case "PassiveAbility":
                case "SinActionModel":
                    return HandleKind.Ability;
                case "ModularSA":
                    return HandleKind.Sa;
                case "AudioClip":
                case "SoundManager":
                    return HandleKind.Sound;
                case "AssetBundle":
                case "TextAsset":
                case "Texture2D":
                case "Sprite":
                    return HandleKind.Asset;
            }

            // Partials/derived types: fall back to a substring probe on the full name.
            var full = target.GetType().FullName ?? string.Empty;
            if (full.Contains("BattleUnitModel")) return HandleKind.Unit;
            if (full.Contains("SkillModel")) return HandleKind.Skill;
            if (full.Contains("CoinModel")) return HandleKind.Coin;
            if (full.Contains("BuffModel")) return HandleKind.Buff;
            if (full.Contains("BattleActionModel")) return HandleKind.Action;
            return HandleKind.Obj;
        }



        // -- liveness ----------------------------------------------------------------------

        /// <summary>Passed to HandleTable so dead objects are dropped instead of dereferenced.</summary>
        public static bool IsAlive(object target)
        {
            if (target == null) return false;
            if (!(target is Il2CppObjectBase il2)) return true; // plain managed object (e.g. boxed data)

            try
            {
                if (il2.WasCollected) return false;
                return il2.Pointer != IntPtr.Zero;
            }
            catch
            {
                // Probing a torn-down object throws; treat as dead.
                return false;
            }
        }
    }
}
