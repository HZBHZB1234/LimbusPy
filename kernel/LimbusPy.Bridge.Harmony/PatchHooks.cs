using System;
using System.Reflection;

namespace LimbusPy.Bridge.Patching
{
    /// <summary>
    /// The static Harmony entry points.
    ///
    /// Harmony needs methods whose parameters follow its injection rules, and it needs the *exact*
    /// result type for ``ref __result``. Since IL2CPP has no JIT (so no Reflection.Emit), the
    /// variants are written out by hand - one prefix that works for every signature, and a small
    /// postfix family covering the result types games actually use.
    ///
    /// Everything funnels into :class:`HarmonyPatchHub.Active`, the single hub per process.
    /// </summary>
    public static class PatchHooks
    {
        internal static HarmonyPatchHub Active;

        // -- prefix -------------------------------------------------------------------

        /// <summary>Runs before any patched method. Returning false skips the original.</summary>
        public static bool Prefix(MethodBase __originalMethod, object __instance, object[] __args)
        {
            var hub = Active;
            if (hub == null) return true;
            try
            {
                return hub.RunPrefix(__originalMethod, __instance, __args);
            }
            catch
            {
                return true; // never break the game because of a patch
            }
        }

        // -- postfix families ----------------------------------------------------------

        public static void Postfix_Void(MethodBase __originalMethod, object __instance, object[] __args)
        {
            var hub = Active;
            if (hub == null) return;
            object boxed = null;
            hub.RunPostfix(__originalMethod, __instance, __args, typeof(void), ref boxed);
        }

        public static void Postfix_Int(MethodBase __originalMethod, object __instance, object[] __args, ref int __result)
        {
            var hub = Active;
            if (hub == null) return;
            object boxed = __result;
            if (hub.RunPostfix(__originalMethod, __instance, __args, typeof(int), ref boxed))
                __result = Convert.ToInt32(boxed);
        }

        public static void Postfix_Long(MethodBase __originalMethod, object __instance, object[] __args, ref long __result)
        {
            var hub = Active;
            if (hub == null) return;
            object boxed = __result;
            if (hub.RunPostfix(__originalMethod, __instance, __args, typeof(long), ref boxed))
                __result = Convert.ToInt64(boxed);
        }

        public static void Postfix_Float(MethodBase __originalMethod, object __instance, object[] __args, ref float __result)
        {
            var hub = Active;
            if (hub == null) return;
            object boxed = __result;
            if (hub.RunPostfix(__originalMethod, __instance, __args, typeof(float), ref boxed))
                __result = Convert.ToSingle(boxed);
        }

        public static void Postfix_Double(MethodBase __originalMethod, object __instance, object[] __args, ref double __result)
        {
            var hub = Active;
            if (hub == null) return;
            object boxed = __result;
            if (hub.RunPostfix(__originalMethod, __instance, __args, typeof(double), ref boxed))
                __result = Convert.ToDouble(boxed);
        }

        public static void Postfix_Bool(MethodBase __originalMethod, object __instance, object[] __args, ref bool __result)
        {
            var hub = Active;
            if (hub == null) return;
            object boxed = __result;
            if (hub.RunPostfix(__originalMethod, __instance, __args, typeof(bool), ref boxed))
                __result = Convert.ToBoolean(boxed);
        }

        public static void Postfix_String(MethodBase __originalMethod, object __instance, object[] __args, ref string __result)
        {
            var hub = Active;
            if (hub == null) return;
            object boxed = __result;
            if (hub.RunPostfix(__originalMethod, __instance, __args, typeof(string), ref boxed))
                __result = boxed as string ?? (boxed == null ? null : Convert.ToString(boxed));
        }

        /// <summary>Any reference / boxed result. Cannot write back to value types.</summary>
        public static void Postfix_Object(MethodBase __originalMethod, object __instance, object[] __args, ref object __result)
        {
            var hub = Active;
            if (hub == null) return;
            object boxed = __result;
            if (hub.RunPostfix(__originalMethod, __instance, __args, typeof(object), ref boxed))
                __result = boxed;
        }

        /// <summary>
        /// Observation only: Harmony can inject the result as ``object`` for exotic return types
        /// (enums, structs) but it cannot be written back. Side effects still work.
        /// </summary>
        public static void Postfix_Observe(MethodBase __originalMethod, object __instance, object[] __args, object __result)
        {
            var hub = Active;
            if (hub == null) return;
            object boxed = __result;
            hub.RunPostfix(__originalMethod, __instance, __args, typeof(void), ref boxed);
        }
    }
}
