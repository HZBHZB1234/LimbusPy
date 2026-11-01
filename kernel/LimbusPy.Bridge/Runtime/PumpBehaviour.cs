using System;
using LimbusPy.Bridge.Core.Bridge;
using UnityEngine;

namespace LimbusPy.Bridge.Runtime
{
    /// <summary>
    /// The only Unity object the kernel owns: a persistent GameObject whose Update() drives the
    /// main-thread queue. Injected through ClassInjector like any other IL2CPP mod, and Unity
    /// dispatches the private Update() message to it (same pattern Modular uses for its own
    /// injected components).
    /// </summary>
    public sealed class PumpBehaviour : MonoBehaviour
    {
        // Required by Il2CppInterop: injected types need the IntPtr constructor.
        public PumpBehaviour(IntPtr pointer) : base(pointer) { }

        private static PumpBehaviour _instance;

        /// <summary>Set by the plugin during Load().</summary>
        internal static MainThreadDispatcher Dispatcher;

        /// <summary>Runs once per frame after the drain (handle sweeping, watchdog ticks, ...).</summary>
        internal static Action OnTick;

        internal static int DrainBatch = 128;
        internal static TimeSpan DrainBudget = TimeSpan.FromMilliseconds(2);

        private int _frame;

        public static PumpBehaviour Ensure()
        {
            if (_instance != null) return _instance;
            var gameObject = new GameObject("LimbusPy.Pump");
            UnityEngine.Object.DontDestroyOnLoad(gameObject);
            _instance = gameObject.AddComponent<PumpBehaviour>();
            return _instance;
        }

        public static int Frame => _instance == null ? 0 : _instance._frame;

        // Unity message: private is fine (Unity dispatches by name).
        private void Update()
        {
            _frame++;
            try
            {
                Dispatcher?.Drain(DrainBatch, DrainBudget);
                OnTick?.Invoke();
            }
            catch (Exception ex)
            {
                // Never let a scripting fault break the game loop.
                Plugin.LogError("pump tick failed: " + ex);
            }
        }

        private void OnApplicationQuit()
        {
            try
            {
                Plugin.Shutdown("application quit");
            }
            catch
            {
                // exiting anyway
            }
        }
    }
}
