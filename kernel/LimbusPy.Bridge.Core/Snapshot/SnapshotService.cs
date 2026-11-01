using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using LimbusPy.Bridge.Core.Protocol;
using LimbusPy.Bridge.Core.Reflect;

namespace LimbusPy.Bridge.Core.Snapshot
{
    /// <summary>
    /// Turns the live battle into one flat, bridgeable message.
    ///
    /// Why this exists: a script that iterates 8 units × 6 stats would otherwise cost 48 round
    /// trips per frame. The kernel walks the game's own dictionary once and returns values, so the
    /// Python façade reads ``unit.hp`` for free (docs/01-protocol.md §6.4).
    ///
    /// Everything here is reflection over the manager, so it works without compile-time knowledge
    /// of the game types and keeps Core Unity-free (the conformance harness drives it with its own
    /// manager object).
    /// </summary>
    public sealed class SnapshotService
    {
        private const BindingFlags Members =
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.FlattenHierarchy;

        private readonly IGlobalRegistry _globals;
        private readonly CallMarshaller _marshaller;
        private readonly IRefStore _store;

        public SnapshotService(IGlobalRegistry globals, CallMarshaller marshaller, IRefStore store)
        {
            _globals = globals;
            _marshaller = marshaller;
            _store = store;
        }

        /// <summary>Name of the singleton that owns the unit dictionary.</summary>
        public string ManagerName { get; set; } = "BattleObjectManager";

        /// <summary>Field/property on the manager that yields the units.</summary>
        public string UnitsMember { get; set; } = "_allUnitDictionary";

        public Dictionary<string, object> Capture()
        {
            var result = Lpcp.Map(
                "round", (object)null,
                "wave", (object)null,
                "units", Array.Empty<object>());

            if (!_globals.TryGet(ManagerName, out var manager) || manager == null)
            {
                result["error"] = "global '" + ManagerName + "' is not registered";
                return result;
            }

            var units = new List<object>();
            var source = ReadMember(manager, UnitsMember);
            if (source == null)
            {
                result["error"] = "manager has no member '" + UnitsMember + "'";
                return result;
            }

            foreach (var entry in EnumerateUnits(source))
            {
                var described = DescribeUnit(entry);
                if (described != null) units.Add(described);
            }

            result["units"] = units.ToArray();
            return result;
        }

        // -- unit extraction ---------------------------------------------------------------

        private IEnumerable<object> EnumerateUnits(object source)
        {
            if (source is IDictionary dictionary)
            {
                foreach (DictionaryEntry entry in dictionary)
                {
                    var model = ExtractModel(entry.Value);
                    if (model != null) yield return model;
                }

                yield break;
            }

            if (source is IEnumerable enumerable)
            {
                foreach (var item in enumerable)
                {
                    var model = ExtractModel(item);
                    if (model != null) yield return model;
                }
            }
        }

        /// <summary>Dictionary values are wrapper structs (``BattleObjectManager.BattleUnit``).</summary>
        private static object ExtractModel(object wrapper)
        {
            if (wrapper == null) return null;
            var type = wrapper.GetType();
            var property = type.GetProperty("Model", Members);
            if (property != null && property.GetIndexParameters().Length == 0)
            {
                try
                {
                    return property.GetValue(wrapper);
                }
                catch
                {
                    return null;
                }
            }

            var field = type.GetField("Model", Members);
            if (field != null)
            {
                try
                {
                    return field.GetValue(wrapper);
                }
                catch
                {
                    return null;
                }
            }

            // Already a model.
            return wrapper;
        }

        private object DescribeUnit(object unit)
        {
            if (unit == null) return null;

            var handle = _store.Store(unit);
            if (handle == 0) return null;

            var kind = _store.KindOf(unit);
            // GetOpponentFaction() names the *opponent*: a player unit reports ENEMY, an enemy
            // reports PLAYER. Kept as a heuristic (name-based enum compare) so it never depends on
            // a numeric enum value; the doctor can flag it if the semantics ever change.
            var opponentFaction = ReadCall(unit, "GetOpponentFaction");
            var isPlayer = string.Equals(opponentFaction?.ToString(), "ENEMY", StringComparison.OrdinalIgnoreCase);

            return Lpcp.Map(
                "ref", Lpcp.Map("t", kind, "h", (long)handle),
                "instance_id", ReadCall(unit, "get_InstanceID") ?? ReadCall(unit, "InstanceID"),
                "origin_id", ReadCall(unit, "GetOriginUnitID"),
                "hp", ReadProperty(unit, "Hp"),
                "max_hp", ReadProperty(unit, "MaxHp"),
                "mp", ReadProperty(unit, "Mp"),
                "level", ReadProperty(unit, "Level"),
                "position", ReadProperty(unit, "Position"),
                "is_dead", ReadCall(unit, "IsDead"),
                "is_break", ReadCall(unit, "IsBreak"),
                "faction", opponentFaction?.ToString(),
                "is_player", isPlayer);
        }

        // -- reflection helpers ------------------------------------------------------------

        private static object ReadMember(object target, string name)
        {
            var type = target.GetType();
            var property = type.GetProperty(name, Members);
            if (property != null && property.GetIndexParameters().Length == 0)
            {
                try
                {
                    return property.GetValue(target);
                }
                catch
                {
                    return null;
                }
            }

            var field = type.GetField(name, Members);
            if (field == null) return null;
            try
            {
                return field.GetValue(target);
            }
            catch
            {
                return null;
            }
        }

        private object ReadProperty(object target, string name)
        {
            var value = ReadMember(target, name);
            if (value == null) return null;
            try
            {
                return _marshaller.ToWire(value);
            }
            catch (Exception)
            {
                return null;
            }
        }

        private object ReadCall(object target, string name)
        {
            var type = target.GetType();
            var method = type.GetMethod(name, Members, null, Type.EmptyTypes, null);
            if (method == null) return null;
            try
            {
                return _marshaller.ToWire(method.Invoke(target, Array.Empty<object>()));
            }
            catch (Exception)
            {
                return null;
            }
        }
    }
}
