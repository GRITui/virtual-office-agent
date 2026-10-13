// VerifyDayNight.cs — re-runnable gate for DayNightCycle (t_9af2fada).
// Batchmode: Unity -batchmode -quit -projectPath unity -executeMethod VerifyDayNight.Run
// Builds a minimal scene (camera + 6 desk lamps + floor + DayNightCycle), applies the rig at
// noon/midnight/dusk, asserts the deterministic lighting values, writes a JSON artifact.
using UnityEngine;
using UnityEditor;
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

public static class VerifyDayNight
{
    public static void Run()
    {
        int fails = RunCore();
        if (fails > 0) throw new Exception("VerifyDayNight: " + fails + " check(s) FAILED — see verify_daynight_result.json");
    }

    static int RunCore()
    {
        var lines = new System.Collections.Generic.List<string>();
        int fails = 0;

        // Build the scene FIRST so DayNightCycle.Awake finds the lamps.
        var camGo = new GameObject("MainCamera");
        camGo.tag = "MainCamera";
        var cam = camGo.AddComponent<Camera>();
        cam.fieldOfView = 40f;
        cam.transform.position = new Vector3(4.8f, 7.2f, 13.8f);
        cam.transform.LookAt(new Vector3(4.8f, 0.9f, 4.2f));
        var floor = GameObject.CreatePrimitive(PrimitiveType.Cube);
        floor.transform.position = new Vector3(4.8f, -0.05f, 4.2f);
        floor.transform.localScale = new Vector3(9.6f, 0.1f, 8.4f);
        var profiles = new[] { "product-owner", "pixel-art-designer", "ux-ui", "software-engineer", "qa-visual", "qa-journey" };
        for (int i = 0; i < 6; i++)
        {
            float cx = (i % 3) * 3.2f + 0.5f;
            float cz = 2.35f + (i / 3) * 3.0f + 1.2f;
            var lgo = new GameObject("Lamp_" + profiles[i]);
            lgo.transform.position = new Vector3(cx, 1.1f, cz);
            var pl = lgo.AddComponent<Light>();
            pl.type = LightType.Point;
            pl.range = 2.5f;
            pl.color = new Color(1f, 0.8f, 0.3f);
        }
        var dnGo = new GameObject("DayNightRig");
        var dn = dnGo.AddComponent<DayNightCycle>(); // Awake wires Sun + Lamp_*

        // ---- noon (hour 12): sun peak, lamps at floor glow ----
        dn.timeOverrideHours = 12f;
        dn.Apply();
        Check(lines, ref fails, "noon.sunIntensity", SunIntensity() == 2.0f, SunIntensity().ToString("F3"));
        Check(lines, ref fails, "noon.ambientSky", RenderSettings.ambientEquatorColor == DayNightCycle.ExposeAmbientSky(12f), RenderSettings.ambientEquatorColor.ToString());
        Check(lines, ref fails, "noon.lampIntensity", LampIntensity() == 0.2f, LampIntensity().ToString("F3"));
        Check(lines, ref fails, "noon.backgroundDay", Camera.main.backgroundColor == DayNightCycle.BG_DAY, Camera.main.backgroundColor.ToString());

        // ---- midnight (hour 0): sun off, lamps full glow (0.95 * 2.0) ----
        dn.timeOverrideHours = 0f;
        dn.Apply();
        Check(lines, ref fails, "midnight.sunIntensity", SunIntensity() == 0f, SunIntensity().ToString("F3"));
        Check(lines, ref fails, "midnight.ambientSky", RenderSettings.ambientEquatorColor == DayNightCycle.ExposeAmbientSky(0f), RenderSettings.ambientEquatorColor.ToString());
        Check(lines, ref fails, "midnight.lampIntensity", Math.Abs(LampIntensity() - 1.9f) < 0.01f, LampIntensity().ToString("F3"));
        Check(lines, ref fails, "midnight.backgroundNight", Camera.main.backgroundColor == DayNightCycle.BG_NIGHT, Camera.main.backgroundColor.ToString());

        // ---- dusk (hour 19.5): sun low 0.3, lamps 0.10+0.85*0.728=0.719 -> 1.438 ----
        dn.timeOverrideHours = 19.5f;
        dn.Apply();
        Check(lines, ref fails, "dusk.sunIntensity", Math.Abs(SunIntensity() - 0.3f) < 0.01f, SunIntensity().ToString("F3"));
        Check(lines, ref fails, "dusk.lampIntensity", Math.Abs(LampIntensity() - 1.438f) < 0.02f, LampIntensity().ToString("F3"));

        string json = "{\n  \"task\": \"t_9af2fada\",\n  \"gate\": \"day-night\",\n  \"failures\": " + fails + ",\n  \"checks\": [\n" +
                      string.Join(",\n", lines.ToArray()) + "\n  ]\n}\n";
        string outPath = Path.Combine(Directory.GetCurrentDirectory(), "verify_daynight_result.json");
        File.WriteAllText(outPath, json);
        Debug.Log("VerifyDayNight: " + (fails == 0 ? "PASS (10/10)" : fails + " FAILURES") + " -> " + outPath);
        return fails;
    }

    static float SunIntensity() => GameObject.Find("Sun").GetComponent<Light>().intensity;
    static float LampIntensity() => GameObject.Find("Lamp_product-owner").GetComponent<Light>().intensity;

    static void Check(List<string> lines, ref int fails, string name, bool ok, string actual)
    {
        if (!ok) fails++;
        lines.Add("    {\"check\": \"" + name + "\", \"pass\": " + (ok ? "true" : "false") + ", \"actual\": \"" + actual + "\"}");
    }
}
