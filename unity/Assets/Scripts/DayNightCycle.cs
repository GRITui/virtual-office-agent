// DayNightCycle.cs — 3D office day/night lighting (t_9af2fada, spec docs/office3d-composition.md)
// Replaces the flat TINT_KEYS tint with real 3D lighting. Same clock as the 2D office:
// 1 real hour = 1 scene day -> hour = (epochMs % 3600000) / 3600000 * 24.
// Drives: Sun (DirectionalLight), hemisphere ambient, background, desk-lamp PointLight intensity.
// Lamp color + live-state are owned by the gameplay binder (t_3d90d98d); this rig sets intensity only.
using UnityEngine;
using System;

[DisallowMultipleComponent]
public class DayNightCycle : MonoBehaviour
{
    // ---- spec keyframes (hour, value) ----
    // Sun: (hour, intensity, elevationDeg, color)
    static readonly float[] SUN_H   = { 0f, 5f, 8f, 12f, 17f, 19.5f, 24f };
    static readonly float[] SUN_INT = { 0f, 0.3f, 1.1f, 2.0f, 1.1f, 0.3f, 0f };
    static readonly float[] SUN_EL  = { 0f, 8f, 30f, 60f, 30f, 8f, 0f };
    static readonly Color[] SUN_COL = {
        Color.black, new Color(0.91f, 0.63f, 0.42f), // 5h dawn #e8a06a
        new Color(1f, 0.949f, 0.816f),               // 8h #fff2d0
        new Color(1f, 0.949f, 0.816f),               // 12h
        new Color(0.847f, 0.541f, 0.353f),           // 17h dusk #d88a5a
        new Color(0.847f, 0.541f, 0.353f),           // 19.5h
        Color.black };
    // Ambient/hemisphere: (hour, sky, ground, intensity) at TINT_KEYS hours 0/5/8/12/17/19.5/22/24
    static readonly float[] AMB_H    = { 0f, 5f, 8f, 12f, 17f, 19.5f, 22f, 24f };
    static readonly Color[] AMB_SKY  = { Hex("#0a1028"), Hex("#6b4a7a"), Hex("#f5d9a8"), Hex("#fff2d0"),
                                        Hex("#f0c890"), Hex("#96463a"), Hex("#10142e"), Hex("#0a1028") };
    static readonly Color[] AMB_GND  = { Hex("#05070c"), Hex("#2a1f2e"), Hex("#6a5a42"), Hex("#8a7a5a"),
                                        Hex("#6a5238"), Hex("#3a221c"), Hex("#070910"), Hex("#05070c") };
    static readonly float[] AMB_INT  = { 0.30f, 0.45f, 0.50f, 0.55f, 0.50f, 0.40f, 0.32f, 0.30f };
    // Background: night #0a1028 -> day #2a241c
    public static readonly Color BG_NIGHT = Hex("#0a1028"), BG_DAY = Hex("#2a241c");
    public static Color ExposeAmbientSky(float hour) => LerpColorAt(AMB_H, AMB_SKY, hour);

    public float lampIntensityScale = 2.0f;
    public float timeOverrideHours = -1f;   // >= 0: freeze the clock (verify gate)
    public float live = 0f;                 // gameplay binder sets 0.15 when a round is active
    public Camera targetCamera;             // null -> Camera.main (Unity 6: no RenderSettings.background)

    Light sun;
    Transform[] lamps;
    float daylight, darkness, lampOn;
    public float hour { get; private set; }

    static Color Hex(string h)
    {
        return new Color(byte.Parse(h.Substring(1, 2), System.Globalization.NumberStyles.HexNumber) / 255f,
                         byte.Parse(h.Substring(3, 2), System.Globalization.NumberStyles.HexNumber) / 255f,
                         byte.Parse(h.Substring(5, 2), System.Globalization.NumberStyles.HexNumber) / 255f);
    }

    void Awake() { Wire(); }

    void Wire()
    {
        if (sun == null)
        {
            var go = GameObject.Find("Sun");
            if (go != null && go.GetComponent<Light>() != null) sun = go.GetComponent<Light>();
            else
            {
                go = new GameObject("Sun");
                sun = go.AddComponent<Light>();
                sun.type = LightType.Directional;
                sun.shadows = LightShadows.Soft;
            }
        }
        if (lamps == null)
        {
            lamps = new Transform[0];
            foreach (var t in GameObject.FindObjectsOfType<Transform>())
                if (t.name.StartsWith("Lamp_") && t.GetComponent<Light>() != null)
                {
                    Array.Resize(ref lamps, lamps.Length + 1);
                    lamps[lamps.Length - 1] = t;
                }
        }
    }

    void Update() { Apply(); }

    public void Apply()
    {
        Wire(); // idempotent; self-heals if Awake timing differs (batchmode)
        hour = timeOverrideHours >= 0f ? timeOverrideHours : CurrentHour();
        daylight = (Mathf.Cos((hour - 12f) * Mathf.PI / 12f) + 1f) / 2f;
        darkness = 1f - daylight;
        lampOn = Mathf.Clamp01((darkness - 0.40f) / 0.40f);

        // Sun: lerp intensity/elevation/color across keyframes; azimuth sweeps -60..+60 over 5..19.5h
        float si = LerpAt(SUN_H, SUN_INT, hour);
        float se = LerpAt(SUN_H, SUN_EL, hour);
        sun.intensity = si;
        sun.color = LerpColorAt(SUN_H, SUN_COL, hour);
        float az = Mathf.Lerp(-60f, 60f, Mathf.Clamp01((hour - 5f) / 14.5f));
        sun.transform.rotation = Quaternion.Euler(se, 180f - az, 0f);

        // Hemisphere ambient + background
        RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Trilight;
        RenderSettings.ambientEquatorColor = LerpColorAt(AMB_H, AMB_SKY, hour);
        RenderSettings.ambientGroundColor = LerpColorAt(AMB_H, AMB_GND, hour);
        RenderSettings.ambientIntensity = LerpAt(AMB_H, AMB_INT, hour);
        // Scene background (per-camera; Unity 6 dropped RenderSettings.background)
        var cam = targetCamera != null ? targetCamera : Camera.main;
        if (cam != null)
        {
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = Color.Lerp(BG_NIGHT, BG_DAY, daylight);
        }

        // Desk lamps: ported glow formula (office.html sceneState)
        float glow = Mathf.Clamp01(0.10f + 0.85f * lampOn + live);
        float li = glow * lampIntensityScale;
        foreach (var l in lamps)
        {
            var pl = l.GetComponent<Light>();
            if (pl != null) pl.intensity = li;
        }
    }

    static float CurrentHour()
    {
        long epochMs = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalMilliseconds;
        return (float)((epochMs % 3600000L) / 3600000.0 * 24.0);
    }

    static float LerpAt(float[] h, float[] v, float hour)
    {
        for (int i = 0; i < h.Length - 1; i++)
            if (hour >= h[i] && hour <= h[i + 1])
            {
                float t = (hour - h[i]) / ((h[i + 1] - h[i]) == 0f ? 1f : (h[i + 1] - h[i]));
                return Mathf.Lerp(v[i], v[i + 1], t);
            }
        return v[v.Length - 1];
    }

    static Color LerpColorAt(float[] h, Color[] v, float hour)
    {
        for (int i = 0; i < h.Length - 1; i++)
            if (hour >= h[i] && hour <= h[i + 1])
            {
                float t = (hour - h[i]) / ((h[i + 1] - h[i]) == 0f ? 1f : (h[i + 1] - h[i]));
                return Color.Lerp(v[i], v[i + 1], t);
            }
        return v[v.Length - 1];
    }
}
