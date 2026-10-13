// VerifyHUD.cs — re-runnable gate for OfficeHUD part 1 (t_9af2fada).
// Batchmode: Unity -batchmode -quit -projectPath unity -executeMethod VerifyHUD.Run
// Builds HUD + camera, feeds a synthetic /office-state payload, asserts labels/board/footer.
using UnityEngine;
using UnityEditor;
using System;
using System.IO;
using System.Collections.Generic;

public static class VerifyHUD
{
    public static void Run()
    {
        int fails = RunCore();
        if (fails > 0) throw new Exception("VerifyHUD: " + fails + " check(s) FAILED — see verify_hud_result.json");
    }

    static int RunCore()
    {
        var lines = new List<string>();
        int fails = 0;

        var camGo = new GameObject("MainCamera");
        camGo.tag = "MainCamera";
        var cam = camGo.AddComponent<Camera>();
        cam.fieldOfView = 40f;
        cam.transform.position = new Vector3(4.8f, 7.2f, 13.8f);
        cam.transform.LookAt(new Vector3(4.8f, 0.9f, 4.2f));

        var hudGo = new GameObject("OfficeHUD");
        var hud = hudGo.AddComponent<OfficeHUD>();

        long now = 1791884422995L;
        var dto = new OfficeHUD.OfficeStateDto
        {
            round_active = true,
            now = now,
            stuck_ms = 8000,
            board = new OfficeHUD.BoardDto { todo = 5, ready = 2, running = 4, blocked = 1, done = 29 },
            agents = new List<OfficeHUD.AgentDto>
            {
                new OfficeHUD.AgentDto { id = "product-owner", state = "idle", seat = 0 },
                new OfficeHUD.AgentDto { id = "pixel-art-designer", state = "idle", seat = 1 },
                new OfficeHUD.AgentDto { id = "ux-ui", state = "idle", seat = 2 },
                new OfficeHUD.AgentDto { id = "software-engineer", state = "working", seat = 3,
                    tool_label = "Read office.html (lines 260-280)", elapsed_ms = 42000, last_delta_ms = now - 1000 },
                new OfficeHUD.AgentDto { id = "qa-visual", state = "working", seat = 4,
                    elapsed_ms = 24000, last_delta_ms = now - 24263 },
                new OfficeHUD.AgentDto { id = "qa-journey", state = "idle", seat = 5 },
            }
        };
        hud.Apply(dto);

        Check(lines, ref fails, "labels.count", hud.LabelCount == 6, hud.LabelCount.ToString());
        Check(lines, ref fails, "label.se3.working", hud.GetLabelStatus(3) == "working · Read office.html (lines 260-280)", hud.GetLabelStatus(3));
        Check(lines, ref fails, "label.se4.stuck", hud.GetLabelStatus(4) == "stuck? no delta 24s", hud.GetLabelStatus(4));
        Check(lines, ref fails, "label.se0.idleEmpty", hud.GetLabelStatus(0) == "", hud.GetLabelStatus(0));
        Check(lines, ref fails, "board.todo", hud.GetBoardCount(0) == "5", hud.GetBoardCount(0));
        Check(lines, ref fails, "board.ready", hud.GetBoardCount(1) == "2", hud.GetBoardCount(1));
        Check(lines, ref fails, "board.done", hud.GetBoardCount(4) == "29", hud.GetBoardCount(4));
        Check(lines, ref fails, "board.todoBars", hud.GetBoardBars(0) == 3, hud.GetBoardBars(0).ToString());
        Check(lines, ref fails, "board.readyBars", hud.GetBoardBars(1) == 2, hud.GetBoardBars(1).ToString());
        Check(lines, ref fails, "footer.headline", hud.FooterText == "2 agents active · qa-visual · stuck? · 24s", hud.FooterText);
        Check(lines, ref fails, "footer.dotLive", hud.FooterDotColor == new Color(126f / 255f, 201f / 255f, 111f / 255f), hud.FooterDotColor.ToString());
        Check(lines, ref fails, "sceneClock", hud.SceneClockNow == "day 16:09", hud.SceneClockNow);

        // idle round: footer falls back
        var idle = new OfficeHUD.OfficeStateDto
        {
            round_active = false, now = now, stuck_ms = 8000,
            board = new OfficeHUD.BoardDto { todo = 0, ready = 0, running = 0, blocked = 0, done = 0 },
            agents = new List<OfficeHUD.AgentDto> { new OfficeHUD.AgentDto { id = "ux-ui", state = "idle", seat = 2 } }
        };
        hud.Apply(idle);
        Check(lines, ref fails, "footer.idle", hud.FooterText == "idle — no active round", hud.FooterText);
        Check(lines, ref fails, "board.zeroed", hud.GetBoardCount(0) == "0" && hud.GetBoardBars(0) == 0, hud.GetBoardCount(0));

        string json = "{\n  \"task\": \"t_9af2fada\",\n  \"gate\": \"hud-part1\",\n  \"failures\": " + fails + ",\n  \"checks\": [\n" +
                      string.Join(",\n", lines.ToArray()) + "\n  ]\n}\n";
        string outPath = Path.Combine(Directory.GetCurrentDirectory(), "verify_hud_result.json");
        File.WriteAllText(outPath, json);
        Debug.Log("VerifyHUD: " + (fails == 0 ? "PASS (" + (lines.Count) + "/" + lines.Count + ")" : fails + " FAILURES") + " -> " + outPath);
        return fails;
    }

    static void Check(List<string> lines, ref int fails, string name, bool ok, string actual)
    {
        if (!ok) fails++;
        lines.Add("    {\"check\": \"" + name + "\", \"pass\": " + (ok ? "true" : "false") + ", \"actual\": \"" + actual + "\"}");
    }
}
