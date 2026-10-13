// OfficeHUD.cs — 3D office HUD, Unity UI Canvas (t_9af2fada, spec docs/office3d-composition.md)
// Part 1: name labels (projected from 3D anchors), board counts (top-right), status footer.
// Data: polls GET /office-state?token= (token from iframe URL query) every 2s; the SE gameplay
// binder (t_3d90d98d) may push richer state via the public Push* API. Drill panel: part 2.
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.Networking;
using System;
using System.Collections;
using System.Collections.Generic;

public class OfficeHUD : MonoBehaviour
{
    // ---- team-room SOT palette (office.html :root) ----
    static readonly Color BG = Hex("#0f1218"), PANEL = Hex("#161b24"), LINE = Hex("#262b34");
    static readonly Color INK = Hex("#e6e9ef"), DIM = Hex("#8b93a3");
    static readonly Color GOOD = Hex("#7ec96f"), BUSY = Hex("#e8b93d"), WORK = Hex("#5db8e0");
    static readonly Color TYPE = Hex("#c98fe0"), BAD = Hex("#e05d5d"), INK_DARK = Hex("#1A1A1A");
    static readonly Color[] COLHEAD = { Hex("#e8b93d"), Hex("#8b93a3"), Hex("#5db8e0"), Hex("#e05d5d"), Hex("#7ec96f") };
    static readonly string[] COLKEYS = { "todo", "ready", "running", "blocked", "done" };
    static readonly string[] COLLABELS = { "TODO", "RDY", "RUN", "BLK", "DONE" };
    static readonly string[] PROFILES = { "product-owner", "pixel-art-designer", "ux-ui", "software-engineer", "qa-visual", "qa-journey" };
    static readonly Color[] PROFILE_COLORS = { Hex("#e8b93d"), Hex("#e05d5d"), Hex("#5db8e0"), Hex("#7ec96f"), Hex("#c98fe0"), Hex("#e09a5d") };

    // ---- /office-state DTO (JsonUtility) ----
    [Serializable] public class BoardDto { public int todo, ready, running, blocked, done; }
    [Serializable] public class AgentDto {
        public string id, color, state, tool_label, card_id, card_title, card_state;
        public int seat; public long elapsed_ms, last_delta_ms, idle_since;
    }
    [Serializable] public class OfficeStateDto {
        public bool round_active; public long now, stuck_ms;
        public List<AgentDto> agents; public BoardDto board;
    }

    class Label { public RectTransform rt; public Text name, status; public Color baseColor; public int seat; }
    class BoardCol { public Text label, count; public Image[] bars; }

    Canvas canvas;
    Camera cam;
    DrillPanel drill;
    readonly Label[] labels = new Label[6];
    readonly BoardCol[] cols = new BoardCol[5];
    Text footerStatus, footerClock, sceneClock;
    Image footerDot;
    OfficeStateDto last;
    float pollTimer;
    const float POLL_S = 2f;

    public static Color Hex(string h)
    {
        return new Color(byte.Parse(h.Substring(1, 2), System.Globalization.NumberStyles.HexNumber) / 255f,
                         byte.Parse(h.Substring(3, 2), System.Globalization.NumberStyles.HexNumber) / 255f,
                         byte.Parse(h.Substring(5, 2), System.Globalization.NumberStyles.HexNumber) / 255f);
    }

    void Awake() { BuildAll(); }

    void BuildAll()
    {
        if (canvas != null) return; // idempotent; self-heals if Awake timing differs (batchmode)
        cam = Camera.main;
        var canvasGo = new GameObject("HUDCanvas");
        canvas = canvasGo.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.ScreenSpaceOverlay;
        canvas.sortingOrder = 10;
        canvasGo.AddComponent<GraphicRaycaster>();
        var scaler = canvasGo.AddComponent<CanvasScaler>();
        scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
        scaler.referenceResolution = new Vector2(1280, 720);
        scaler.matchWidthOrHeight = 0.5f;
        BuildLabels();
        BuildBoardCounts();
        BuildFooter();
        var dgo = new GameObject("DrillPanel");
        drill = dgo.AddComponent<DrillPanel>();
        pollTimer = -1f; // fetch immediately
    }

    void Update()
    {
        pollTimer += Time.deltaTime;
        if (pollTimer >= POLL_S) { pollTimer = 0f; StartCoroutine(PollOfficeState()); }
        if (Input.GetKeyDown(KeyCode.Escape) && drill != null && drill.IsOpen) drill.Close();
        UpdateLabelPositions();
    }

    // ---------- drill-in ----------
    public void OpenDrill(int i)
    {
        if (drill == null || last == null) return;
        AgentDto a = null;
        if (last.agents != null)
            foreach (var x in last.agents) if (x.id == PROFILES[i]) { a = x; break; }
        drill.OpenAgent(PROFILES[i], a, last);
    }

    public void CloseDrill() { if (drill != null) drill.Close(); }

    public void PushStreamLine(string profile, string kind, string text, long tsMs)
    {
        if (drill != null) drill.PushStreamLine(profile, kind, text, tsMs);
    }

    // ---------- name labels ----------
    void BuildLabels()
    {
        for (int i = 0; i < 6; i++)
        {
            var go = new GameObject("Label_" + PROFILES[i]);
            var rt = (RectTransform)go.AddComponent<RectTransform>();
            rt.sizeDelta = new Vector2(150, 40);
            rt.anchorMin = new Vector2(0.5f, 0.5f);
            rt.anchorMax = new Vector2(0.5f, 0.5f);
            rt.SetParent(canvas.transform, false);
            var img = go.AddComponent<Image>();
            img.color = PROFILE_COLORS[i];
            var name = MakeText("name", PROFILES[i], 12, FontStyle.Bold, INK_DARK);
            name.rectTransform.SetParent(rt, false);
            Stretch(name.rectTransform, new Vector2(0, 1), new Vector2(0, 1), new Vector2(0, 10));
            var status = MakeText("status", "", 10, FontStyle.Normal, INK_DARK);
            status.rectTransform.SetParent(rt, false);
            Stretch(status.rectTransform, new Vector2(0, 0), new Vector2(0, 0), new Vector2(0, -12));
            var btn = go.AddComponent<Button>();
            btn.targetGraphic = img;
            int idx = i;
            btn.onClick.AddListener(() => OpenDrill(idx));
            labels[i] = new Label { rt = rt, name = name, status = status, baseColor = PROFILE_COLORS[i], seat = i };
        }
    }

    Vector3 AnchorFor(int i)
    {
        // label 0.35 m above the avatar head (avatar height_m = 1.15 per avatars/manifest.json)
        const float LABEL_Y = 1.5f;
        var av = GameObject.Find("Avatar_" + PROFILES[i]);
        if (av != null) return new Vector3(av.transform.position.x, LABEL_Y, av.transform.position.z);
        float x = (i % 3) * 3.2f + 1.6f;          // cell origin + (32,14)px @ 0.05 m/px
        float z = 2.35f + (i / 3) * 3.0f + 0.7f;
        return new Vector3(x, LABEL_Y, z);
    }

    void UpdateLabelPositions()
    {
        if (cam == null) cam = Camera.main;
        if (cam == null) return;
        for (int i = 0; i < 6; i++)
        {
            bool openHere = drill != null && drill.IsOpen && drill.OpenProfile == PROFILES[i];
            var p = cam.WorldToScreenPoint(AnchorFor(i));
            bool behind = p.z < 0f;
            labels[i].rt.gameObject.SetActive(!behind && !openHere);
            if (behind) continue;
            var pos = new Vector2(p.x, p.y);
            // CanvasScaler 0.5: convert screen px -> canvas units
            float sx = (float)Screen.width / 1280f * 2f * 0.5f + (float)Screen.width / 1280f * 0.5f;
            float sy = (float)Screen.height / 720f * 2f * 0.5f + (float)Screen.height / 720f * 0.5f;
            labels[i].rt.anchoredPosition = new Vector2((p.x / sx - 0.5f) * 1280f, (0.5f - p.y / sy) * 720f);
        }
    }

    // ---------- board counts (top-right) ----------
    void BuildBoardCounts()
    {
        var panelGo = new GameObject("BoardCounts");
        var prt = (RectTransform)panelGo.AddComponent<RectTransform>();
        prt.SetParent(canvas.transform, false);
        prt.anchorMin = new Vector2(1, 1); prt.anchorMax = new Vector2(1, 1);
        prt.pivot = new Vector2(1, 1);
        prt.anchoredPosition = new Vector2(-16, -16);
        prt.sizeDelta = new Vector2(300, 130);
        var img = panelGo.AddComponent<Image>();
        img.color = new Color(PANEL.r, PANEL.g, PANEL.b, 0.88f);
        for (int c = 0; c < 5; c++)
        {
            float x0 = 14f + c * 58f;
            var head = MakeImage("head" + c, COLHEAD[c]);
            head.rectTransform.SetParent(prt, false);
            head.rectTransform.anchorMin = new Vector2(0, 1); head.rectTransform.anchorMax = new Vector2(0, 1);
            head.rectTransform.pivot = new Vector2(0, 1);
            head.rectTransform.anchoredPosition = new Vector2(x0, -10);
            head.rectTransform.sizeDelta = new Vector2(44, 4);
            var lab = MakeText("lab" + c, COLLABELS[c], 10, FontStyle.Bold, DIM);
            lab.rectTransform.SetParent(prt, false);
            lab.alignment = TextAnchor.MiddleCenter;
            lab.rectTransform.anchorMin = new Vector2(0, 1); lab.rectTransform.anchorMax = new Vector2(0, 1);
            lab.rectTransform.pivot = new Vector2(0, 1);
            lab.rectTransform.anchoredPosition = new Vector2(x0 - 8, -16);
            lab.rectTransform.sizeDelta = new Vector2(60, 14);
            var cnt = MakeText("cnt" + c, "0", 22, FontStyle.Bold, INK);
            cnt.rectTransform.SetParent(prt, false);
            cnt.alignment = TextAnchor.MiddleCenter;
            cnt.rectTransform.anchorMin = new Vector2(0, 1); cnt.rectTransform.anchorMax = new Vector2(0, 1);
            cnt.rectTransform.pivot = new Vector2(0, 1);
            cnt.rectTransform.anchoredPosition = new Vector2(x0 - 8, -32);
            cnt.rectTransform.sizeDelta = new Vector2(60, 28);
            var bars = new Image[3];
            for (int b = 0; b < 3; b++)
            {
                bars[b] = MakeImage("bar" + c + "_" + b, new Color(48f / 255f, 55f / 255f, 68f / 255f, 1f));
                bars[b].rectTransform.SetParent(prt, false);
                bars[b].rectTransform.anchorMin = new Vector2(0, 1); bars[b].rectTransform.anchorMax = new Vector2(0, 1);
                bars[b].rectTransform.pivot = new Vector2(0, 1);
                bars[b].rectTransform.anchoredPosition = new Vector2(x0, -64 - b * 8);
                bars[b].rectTransform.sizeDelta = new Vector2(44, 4);
            }
            cols[c] = new BoardCol { label = lab, count = cnt, bars = bars };
        }
    }

    void SetBoardCounts(BoardDto b)
    {
        int[] vals = { b.todo, b.ready, b.running, b.blocked, b.done };
        for (int c = 0; c < 5; c++)
        {
            cols[c].count.text = Mathf.Max(0, vals[c]).ToString();
            for (int k = 0; k < 3; k++)
                cols[c].bars[k].gameObject.SetActive(k < Mathf.Min(Mathf.Max(0, vals[c]), 3));
        }
    }

    // ---------- status footer ----------
    void BuildFooter()
    {
        var barGo = new GameObject("Footer");
        var brt = (RectTransform)barGo.AddComponent<RectTransform>();
        brt.SetParent(canvas.transform, false);
        brt.anchorMin = new Vector2(0, 0); brt.anchorMax = new Vector2(1, 0);
        brt.pivot = new Vector2(0.5f, 0);
        brt.anchoredPosition = new Vector2(0, 6);
        brt.sizeDelta = new Vector2(0, 30);
        var img = barGo.AddComponent<Image>();
        img.color = new Color(PANEL.r, PANEL.g, PANEL.b, 0.92f);
        footerDot = MakeImage("dot", DIM);
        footerDot.rectTransform.SetParent(brt, false);
        footerDot.rectTransform.anchorMin = new Vector2(0, 0.5f); footerDot.rectTransform.anchorMax = new Vector2(0, 0.5f);
        footerDot.rectTransform.pivot = new Vector2(0, 0.5f);
        footerDot.rectTransform.anchoredPosition = new Vector2(14, 0);
        footerDot.rectTransform.sizeDelta = new Vector2(8, 8);
        footerStatus = MakeText("status", "idle — no active round", 12, FontStyle.Normal, INK);
        footerStatus.rectTransform.SetParent(brt, false);
        footerStatus.alignment = TextAnchor.MiddleLeft;
        footerStatus.rectTransform.anchorMin = new Vector2(0, 0.5f); footerStatus.rectTransform.anchorMax = new Vector2(0, 0.5f);
        footerStatus.rectTransform.pivot = new Vector2(0, 0.5f);
        footerStatus.rectTransform.anchoredPosition = new Vector2(30, 0);
        footerStatus.rectTransform.sizeDelta = new Vector2(600, 24);
        sceneClock = MakeText("scene", "", 12, FontStyle.Normal, DIM);
        sceneClock.rectTransform.SetParent(brt, false);
        sceneClock.alignment = TextAnchor.MiddleRight;
        sceneClock.rectTransform.anchorMin = new Vector2(1, 0.5f); sceneClock.rectTransform.anchorMax = new Vector2(1, 0.5f);
        sceneClock.rectTransform.pivot = new Vector2(1, 0.5f);
        sceneClock.rectTransform.anchoredPosition = new Vector2(-120, 0);
        sceneClock.rectTransform.sizeDelta = new Vector2(100, 24);
        footerClock = MakeText("clock", "", 12, FontStyle.Normal, DIM);
        footerClock.rectTransform.SetParent(brt, false);
        footerClock.alignment = TextAnchor.MiddleRight;
        footerClock.rectTransform.anchorMin = new Vector2(1, 0.5f); footerClock.rectTransform.anchorMax = new Vector2(1, 0.5f);
        footerClock.rectTransform.pivot = new Vector2(1, 0.5f);
        footerClock.rectTransform.anchoredPosition = new Vector2(-12, 0);
        footerClock.rectTransform.sizeDelta = new Vector2(100, 24);
    }

    // ---------- data ----------
    IEnumerator PollOfficeState()
    {
        string token = TokenFromUrl();
        var req = new UnityWebRequest("http://127.0.0.1:8651/office-state?token=" + Uri.EscapeDataString(token), "GET");
        var op = req.SendWebRequest();
        while (!op.isDone) yield return null;
        if (req.result == UnityWebRequest.Result.Success)
        {
            var dto = JsonUtility.FromJson<OfficeStateDto>(req.downloadHandler.text);
            if (dto != null) Apply(dto);
        }
        req.Dispose();
    }

    public void Apply(OfficeStateDto dto)
    {
        BuildAll();
        last = dto;
        if (dto.board != null) SetBoardCounts(dto.board);
        var active = new List<AgentDto>();
        if (dto.agents != null)
            foreach (var a in dto.agents)
            {
                int idx = System.Array.IndexOf(PROFILES, a.id);
                if (idx < 0) continue;
                labels[idx].status.text = StateText(a, dto);
                if (!string.IsNullOrEmpty(a.state) && a.state != "idle") active.Add(a);
                if (drill != null && drill.IsOpen && drill.OpenProfile == a.id) drill.Refresh(a, dto);
            }
        // footer headline (ported from office.html render())
        footerDot.color = active.Count > 0 ? GOOD : DIM;
        if (active.Count > 0)
        {
            var h = active[active.Count - 1]; // most-recently-active (last in array order)
            bool hstuck = h.state != "done" && dto.stuck_ms > 0 && dto.now - h.last_delta_ms > dto.stuck_ms;
            string w = hstuck ? "stuck?" : h.state;
            string count = active.Count > 1 ? active.Count + " agents active · " : "";
            footerStatus.text = count + h.id + " · " + w +
                (w == "working" && !string.IsNullOrEmpty(h.tool_label) ? " · " + h.tool_label : "") +
                " · " + FmtElapsed(h.elapsed_ms);
            footerClock.text = "last delta " + FmtElapsed(dto.now - h.last_delta_ms) + " ago";
        }
        else
        {
            footerStatus.text = dto.round_active ? "round active · waiting for a turn" : "idle — no active round";
            footerClock.text = "";
        }
        sceneClock.text = SceneClockText(dto.now);
    }

    public static string StateText(AgentDto a, OfficeStateDto dto)
    {
        if (string.IsNullOrEmpty(a.state) || a.state == "idle") return "";
        bool stuck = a.state != "done" && dto.stuck_ms > 0 && dto.now - a.last_delta_ms > dto.stuck_ms;
        if (stuck) return "stuck? no delta " + FmtElapsed(dto.now - a.last_delta_ms);
        switch (a.state)
        {
            case "working": return "working" + (string.IsNullOrEmpty(a.tool_label) ? "" : " · " + a.tool_label);
            case "typing": return "typing " + FmtElapsed(a.elapsed_ms);
            case "done": return "done";
            default: return "thinking " + FmtElapsed(a.elapsed_ms);
        }
    }

    public static string FmtElapsed(long ms)
    {
        return Math.Max(0, ms / 1000) + "s";
    }

    public static string FmtAgo(long tsMs, long nowMs)
    {
        if (tsMs <= 0) return "—";
        long s = Math.Max(0, (nowMs - tsMs) / 1000);
        if (s < 60) return s + "s ago";
        long m = s / 60;
        if (m < 60) return m + "m ago";
        long h = m / 60;
        if (h < 24) return h + "h ago";
        return (h / 24) + "d ago";
    }

    public static string SceneClockText(long nowMs)
    {
        // 1 real hour = 1 scene day (office.html DAY_MS)
        float hour = (float)((nowMs % 3600000L) / 3600000.0 * 24.0);
        string phase = hour < 5 ? "night" : hour < 8 ? "dawn" : hour < 17 ? "day" : hour < 19.5f ? "dusk" : "night";
        return phase + " " + (int)hour + ":" + ((int)((hour % 1f) * 60f)).ToString("D2");
    }

    static string TokenFromUrl()
    {
        string url = Application.absoluteURL;
        int i = url.IndexOf("token=");
        if (i < 0) return "";
        string t = url.Substring(i + 6);
        int amp = t.IndexOf('&');
        if (amp >= 0) t = t.Substring(0, amp);
        return Uri.UnescapeDataString(t);
    }

    // ---------- test / integration accessors ----------
    public DrillPanel Drill => drill;
    public int LabelCount => labels.Length;
    public string GetLabelStatus(int i) => labels[i].status.text;
    public bool LabelVisible(int i) => labels[i].rt.gameObject.activeSelf;
    public string GetBoardCount(int c) => cols[c].count.text;
    public int GetBoardBars(int c) { int n = 0; foreach (var b in cols[c].bars) if (b.gameObject.activeSelf) n++; return n; }
    public string FooterText => footerStatus.text;
    public Color FooterDotColor => footerDot.color;
    public string SceneClockNow => sceneClock.text;

    // ---------- helpers ----------
    public static Text MakeTextPublic(string name, string text, int size, FontStyle style, Color color) => MakeText(name, text, size, style, color);
    public static Image MakeImagePublic(string name, Color color) => MakeImage(name, color);

    public static Button MakeButtonPublic(string name, string text, int size, Color color, UnityEngine.Events.UnityAction onClick)
    {
        var go = new GameObject(name);
        var rt = (RectTransform)go.AddComponent<RectTransform>();
        var b = go.AddComponent<Button>();
        b.targetGraphic = go.AddComponent<Image>();
        b.onClick.AddListener(onClick);
        var t = MakeText("txt", text, size, FontStyle.Bold, color);
        t.rectTransform.SetParent(rt, false);
        t.rectTransform.anchorMin = Vector2.zero;
        t.rectTransform.anchorMax = Vector2.one;
        t.rectTransform.offsetMin = Vector3.zero;
        t.rectTransform.offsetMax = Vector3.zero;
        return b;
    }

    static Text MakeText(string name, string text, int size, FontStyle style, Color color)
    {
        var go = new GameObject(name);
        var rt = (RectTransform)go.AddComponent<RectTransform>();
        var t = go.AddComponent<Text>();
        t.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        t.text = text; t.fontSize = size; t.fontStyle = style; t.color = color;
        t.alignment = TextAnchor.MiddleCenter;
        t.horizontalOverflow = HorizontalWrapMode.Overflow;
        return t;
    }

    static Image MakeImage(string name, Color color)
    {
        var go = new GameObject(name);
        var rt = (RectTransform)go.AddComponent<RectTransform>();
        var img = go.AddComponent<Image>();
        img.color = color;
        return img;
    }

    static void Stretch(RectTransform rt, Vector2 aMin, Vector2 aMax, Vector2 pos)
    {
        rt.anchorMin = aMin; rt.anchorMax = aMax;
        rt.pivot = new Vector2(0.5f, 0.5f);
        rt.anchoredPosition = pos;
        rt.sizeDelta = new Vector2(150, 16);
    }
}
