using System;
using System.Collections.Generic;
using System.IO;
using HarmonyLib;
using Il2CppInterop.Runtime.Injection;
using UnityEngine;

namespace LimbusPy.Ops
{
    /// <summary>
    /// 自定义 Buff 的「真注入」：往游戏的 <c>BUFF_UNIQUE_KEYWORD</c> 枚举里加值，并接管图标。
    ///
    /// 技术路线由 Lethe 实证（<c>Lethe.EnumInjections/EnumInjectionGenericHandler.cs</c>）：
    /// 这不是"往 BuffStaticData 里插一条记录"，而是**三件事的组合**：
    /// <list type="number">
    /// <item>枚举注入（本类的主体）；</item>
    /// <item>图标：patch <c>BuffStaticData.GetBuffIconSprite</c> / <c>GetBuffTypoIconSprite</c>；</item>
    /// <item>本地化：另走 locale 管线（未实现）。</item>
    /// </list>
    ///
    /// **id 基址用 24576（0x6000）、步长 1**，而不是 Lethe 的 <c>1+4096+2k</c>：
    /// 我核过原版枚举共 2124 个成员、最大 2123 且就是 <c>None</c> 哨兵，
    /// 1000→2123 密排；24576 有约 2.2 万个空号，天然跨过哨兵，也能和 Lethe 共存。
    ///
    /// 整类 fail-open：任何一步失败都只是"注入不可用"，绝不抛给内核（A 层的独立失效原则）。
    /// </summary>
    internal static class BuffInject
    {
        /// <summary>自定义 Buff 的 id 基址。</summary>
        internal const int BaseId = 24576;

        /// <summary>一次注入的名额上限（留出余量，避免挤坏原版枚举的假设）。</summary>
        internal const int Capacity = 512;

        private static readonly Dictionary<string, int> Ids =
            new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

        private static readonly Dictionary<int, string> Keys = new Dictionary<int, string>();

        private static readonly Dictionary<int, string> IconFiles = new Dictionary<int, string>();

        private static readonly Dictionary<int, Sprite> SpriteCache = new Dictionary<int, Sprite>();

        /// <summary>注入是否真的成功了（失败时 A 层其余部分照常工作）。</summary>
        internal static bool Available { get; private set; }

        /// <summary>诊断信息。</summary>
        internal static List<string> Problems { get; } = new List<string>();

        /// <summary>已注入的数量。</summary>
        internal static int Count => Ids.Count;

        /// <summary>
        /// 把一组关键字注入枚举。
        ///
        /// 幂等：已注入的名字会跳过。返回 <c>{keyword: id}</c>。
        /// </summary>
        internal static Dictionary<string, int> Inject(IReadOnlyList<string> keywords,
            IReadOnlyList<string> icons)
        {
            var result = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            if (keywords == null || keywords.Count == 0) return result;

            try
            {
                var payload = new Dictionary<string, object>();
                var next = BaseId;
                for (var i = 0; i < keywords.Count && i < Capacity; i++)
                {
                    var keyword = Normalize(keywords[i]);
                    if (keyword.Length == 0) continue;
                    var icon = icons != null && i < icons.Count ? icons[i] : null;

                    if (!Ids.TryGetValue(keyword, out var id))
                    {
                        while (Keys.ContainsKey(next)) next++;
                        id = next++;
                        Ids[keyword] = id;
                        Keys[id] = keyword;
                        payload[keyword] = id;
                        if (!string.IsNullOrEmpty(icon)) IconFiles[id] = icon;
                    }

                    result[keyword] = id;
                }

                if (payload.Count > 0)
                {
                    // Lethe 同款调用：打的是「枚举名 → 值」的字典，值是 int。
                    EnumInjector.InjectEnumValues<BUFF_UNIQUE_KEYWORD>(payload);
                }

                Available = true;
            }
            catch (Exception ex)
            {
                Available = false;
                Problems.Add("enum inject: " + ex.GetType().Name + ": " + ex.Message);
            }

            return result;
        }

        /// <summary>关键字 → 注入的 id（未注入返回 false）。</summary>
        internal static bool TryResolve(string keyword, out int id)
        {
            id = 0;
            return keyword != null && Ids.TryGetValue(Normalize(keyword), out id);
        }

        /// <summary>
        /// 关键字 → 枚举值。先用注入表，再退回原版 <see cref="Enum.TryParse{TEnum}(string, bool, out TEnum)"/>。
        ///
        /// 这条回退很关键：真机上 <c>Enum.TryParse</c> 能不能认注入成员还没验证，
        /// 认不到时我们必须还能处理原版关键字。
        /// </summary>
        internal static bool TryParse(string keyword, out BUFF_UNIQUE_KEYWORD value)
        {
            value = BUFF_UNIQUE_KEYWORD.None;
            if (string.IsNullOrEmpty(keyword)) return false;
            if (TryResolve(keyword, out var id))
            {
                value = (BUFF_UNIQUE_KEYWORD)id;
                return true;
            }

            try
            {
                return Enum.TryParse(Normalize(keyword), true, out value);
            }
            catch
            {
                return false;
            }
        }

        /// <summary>注册图标的补丁点。拿不到图就放行，因此可以和 Lethe 共存。</summary>
        internal static void InstallIcons(Harmony harmony, string iconRoot)
        {
            if (harmony == null) return;
            try
            {
                IconRoot = iconRoot ?? string.Empty;
                var type = typeof(BuffStaticData);
                var byKeyword = type.GetMethod("GetBuffIconSprite");
                var byTypo = type.GetMethod("GetBuffTypoIconSprite");
                var prefix = new HarmonyMethod(typeof(BuffInject).GetMethod(nameof(BuffIconPrefix)));
                if (byKeyword != null) harmony.Patch(byKeyword, prefix: prefix);
                if (byTypo != null) harmony.Patch(byTypo, prefix: prefix);
            }
            catch (Exception ex)
            {
                Problems.Add("icon patch: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        /// <summary>图标根目录（<c>custom_sprites/buffIcon_&lt;key&gt;.png</c>）。</summary>
        internal static string IconRoot = string.Empty;

        /// <summary>
        /// Harmony prefix：能给出图就返回 <c>true</c>（跳过原方法），否则 <c>false</c> 放行。
        ///
        /// 返回 false 放行是刻意设计 —— 我们没图时让原版逻辑照常跑，
        /// 这样即使 Lethe 也打了同一个点，两家的补丁也不会互相毁掉。
        /// </summary>
        public static bool BuffIconPrefix(BuffStaticData __instance, ref Sprite __result)
        {
            try
            {
                if (__instance == null) return false;
                // 真实访问器是 GetKeyword()（full0 核对过；没有 m_BuffKeyword 这种字段）
                var keyword = OpsArgs.SafeInt(() => (int)__instance.GetKeyword());
                if (!Keys.ContainsKey(keyword)) return false;
                var sprite = SpriteFor(keyword);
                if (sprite == null) return false;
                __result = sprite;
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static Sprite SpriteFor(int id)
        {
            if (SpriteCache.TryGetValue(id, out var cached)) return cached;
            if (!IconFiles.TryGetValue(id, out var file) || string.IsNullOrEmpty(file)) return null;

            try
            {
                var path = Path.IsPathRooted(file) ? file : Path.Combine(IconRoot ?? "", file);
                if (!File.Exists(path)) return null;

                var bytes = File.ReadAllBytes(path);
                var texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                if (!ImageConversion.LoadImage(texture, bytes)) return null;
                texture.name = "limbuspy_buff_" + id;
                var sprite = Sprite.Create(texture, new Rect(0f, 0f, texture.width, texture.height),
                    new Vector2(0.5f, 0.5f));
                sprite.name = texture.name;
                SpriteCache[id] = sprite;
                return sprite;
            }
            catch (Exception ex)
            {
                Problems.Add("icon load #" + id + ": " + ex.GetType().Name);
                return null;
            }
        }

        private static string Normalize(string keyword)
            => (keyword ?? string.Empty).Trim().Replace(' ', '_').ToUpperInvariant();

        /// <summary>卸载时清空（枚举本身无法回退，但至少不再提供查表）。</summary>
        internal static void Clear()
        {
            Ids.Clear();
            Keys.Clear();
            IconFiles.Clear();
            SpriteCache.Clear();
            Available = false;
        }
    }
}
