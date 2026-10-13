// DrillPanel.cs — 3D office drill-in panel, Unity UI Canvas (t_9af2fada, spec docs/office3d-composition.md)
// Port of office.html drill panel (t_bb332f8e), agent mode. Desktop (>=521px): right side panel,
// width min(380px, 44vw), full height, slide-in 0.16s. Mobile (<=520px): bottom sheet,
// height min(66vh, 560px), sheet-up 0.18s. Sections: now / current card / why stuck / live turn stream.
// Stream lines are pushed by the gameplay binder (t_3d90d98d) via PushStreamLine.
using UnityEngine;
using UnityEngine.UI;
using System;
using System.Collections;
using System.Collections.Generic;

public class DrillPanel : MonoBehaviour
{
    static readonly Color PANEL = OfficeHUD.Hex("#161b24"), LINE = OfficeHUD.Hex("#262b34");
    static readonly Color INK = OfficeHUD.Hex("#e6e9ef"), DIM = OfficeHUD.Hex("#8b93a3");
    static readonly Color GOOD = OfficeHUD.Hex("#7ec96f"), BUSY = OfficeHUD.Hex("#e8b93d"), WORK = OfficeHUD.Hex("#5db8e0");
    static readonly Color TYPE = OfficeHUD.Hex("#c98fe0"), BAD = OfficeHUD.Hex("#e05d5d");

    struct StreamLine { public long ts; public string kind, text; }

    Canvas canvas;
    RectTransform root, head, bodyContent;
    Image grab;
    Text title, chip, closeTxt;
    Text kvState, kvTool, kvElapsed, kvLastDelta, kvCard, kvCardStatus, stuckText;
    RectTransform stuckSec;
    readonly Text[] streamLines = new Text[12];
    Text streamEmpty;
    Button closeBtn;
    ContentSizeFitter fitter;

    string openProfile;
    OfficeHUD.AgentDto openAgent;
    OfficeHUD.OfficeStateDto openDto;
    bool stuck;
    readonly Dictionary<string, List<StreamLine>> streams = new Dictionary<string, List<StreamLine>>();
    Coroutine anim;

    public bool IsOpen => openProfile != null;
    public string OpenProfile => openProfile;
    public bool PanelVisible => root != null && root.gameObject.activeSelf;
    public string TitleText => title != null ? title.text : "";
    public string ChipText => chip != null ? chip.text : "";
    public string KvStateText => kvState != null ? kvState.text : "";
    public string KvCardText => kvCard != null ? kvCard.text : "";
    public string KvCardStatusText => kvCardStatus != null ? kvCardStatus.text : "";
    public bool StuckSectionVisible => stuckSec != null && stuckSec.gameObject.activeSelf;
    public string StuckBodyText => stuckText != null ? stuckText.text : "";
    public string FirstStreamLine => streamLines != null && streamLines.Length > 0 ? streamLines[0].text : "";
    public bool StreamEmptyVisible => streamEmpty != null && streamEmpty.gameObject.activeSelf;

    void Awake()
    {
        canvas = FindObjectOfType<Canvas>();
        if (canvas == null)
        {
            var cgo = new GameObject("HUDCanvas");
            canvas = cgo.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 11;
            cgo.AddComponent<GraphicRaycaster>();
            var sc = cgo.AddComponent<CanvasScaler>();
            sc.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            sc.referenceResolution = new Vector2(1280, 720);
            sc.matchWidthOrHeight = 0.5f;
        }
        Build();
    }

    void Build()
    {
        if (canvas == null)
        {
            canvas = FindObjectOfType<Canvas>();
            if (canvas == null)
            {
                var cgo = new GameObject("HUDCanvas");
                canvas = cgo.AddComponent<Canvas>();
                canvas.renderMode = RenderMode.ScreenSpaceOverlay;
                canvas.sortingOrder = 11;
                cgo.AddComponent<GraphicRaycaster>();
                var sc = cgo.AddComponent<CanvasScaler>();
                sc.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
                sc.referenceResolution = new Vector2(1280, 720);
                sc.matchWidthOrHeight = 0.5f;
            }
        }
        var go = new GameObject("DrillPanel");
        root = (RectTransform)go.AddComponent<RectTransform>();
        root.SetParent(canvas.transform, false);
        var img = go.AddComponent<Image>();
        img.color = new Color(PANEL.r, PANEL.g, PANEL.b, 0.97f);
        go.AddComponent<Button>(); // block raycasts to the scene behind

        // head
        var headGo = new GameObject("head");
        head = (RectTransform)headGo.AddComponent<RectTransform>();
        head.SetParent(root, false);
        head.anchorMin = new Vector2(0, 1); head.anchorMax = new Vector2(1, 1);
        head.pivot = new Vector2(0.5f, 1);
        head.anchoredPosition = new Vector2(0, 0);
        head.sizeDelta = new Vector2(0, 44);
        var headImg = headGo.AddComponent<Image>();
        headImg.color = new Color(LINE.r, LINE.g, LINE.b, 0.6f);
        grab = OfficeHUD.MakeImagePublic("grab", DIM);
        grab.rectTransform.SetParent(head, false);
        grab.rectTransform.anchorMin = new Vector2(0.5f, 0.5f); grab.rectTransform.anchorMax = new Vector2(0.5f, 0.5f);
        grab.rectTransform.anchoredPosition = new Vector2(0, 6);
        grab.rectTransform.sizeDelta = new Vector2(34, 4);
        grab.gameObject.SetActive(false);
        title = OfficeHUD.MakeTextPublic("title", "", 13, FontStyle.Bold, INK);
        title.rectTransform.SetParent(head, false);
        title.alignment = TextAnchor.MiddleLeft;
        title.rectTransform.anchorMin = new Vector2(0, 0.5f); title.rectTransform.anchorMax = new Vector2(0, 0.5f);
        title.rectTransform.pivot = new Vector2(0, 0.5f);
        title.rectTransform.anchoredPosition = new Vector2(12, 0);
        title.rectTransform.sizeDelta = new Vector2(200, 24);
        chip = OfficeHUD.MakeTextPublic("chip", "idle", 10, FontStyle.Bold, DIM);
        chip.rectTransform.SetParent(head, false);
        chip.alignment = TextAnchor.MiddleCenter;
        chip.rectTransform.anchorMin = new Vector2(1, 0.5f); chip.rectTransform.anchorMax = new Vector2(1, 0.5f);
        chip.rectTransform.pivot = new Vector2(1, 0.5f);
        chip.rectTransform.anchoredPosition = new Vector2(-56, 0);
        chip.rectTransform.sizeDelta = new Vector2(44, 18);
        var chipBg = OfficeHUD.MakeImagePublic("chipBg", new Color(DIM.r, DIM.g, DIM.b, 0.25f));
        chipBg.rectTransform.SetParent(head, false);
        chipBg.rectTransform.anchorMin = new Vector2(1, 0.5f); chipBg.rectTransform.anchorMax = new Vector2(1, 0.5f);
        chipBg.rectTransform.pivot = new Vector2(1, 0.5f);
        chipBg.rectTransform.anchoredPosition = new Vector2(-56, 0);
        chipBg.rectTransform.sizeDelta = new Vector2(44, 18);
        closeBtn = OfficeHUD.MakeButtonPublic("close", "×", 16, INK, OnClose);
        closeBtn.GetComponent<RectTransform>().SetParent(head, false);
        var crt = closeBtn.GetComponent<RectTransform>();
        crt.anchorMin = new Vector2(1, 0.5f); crt.anchorMax = new Vector2(1, 0.5f);
        crt.pivot = new Vector2(1, 0.5f);
        crt.anchoredPosition = new Vector2(-10, 0);
        crt.sizeDelta = new Vector2(28, 28);

        // body (scroll)
        var bodyGo = new GameObject("body");
        var bodyRt = (RectTransform)bodyGo.AddComponent<RectTransform>();
        bodyRt.SetParent(root, false);
        bodyRt.anchorMin = Vector2.zero; bodyRt.anchorMax = Vector2.one;
        bodyRt.offsetMin = new Vector3(12, 12); bodyRt.offsetMax = new Vector3(-12, -48);
        var scroll = bodyGo.AddComponent<ScrollRect>();
        scroll.horizontal = false; scroll.movementType = ScrollRect.MovementType.Clamped;
        bodyContent = (RectTransform)new GameObject("content").AddComponent<RectTransform>();
        bodyContent.SetParent(bodyRt, false);
        bodyContent.anchorMin = new Vector2(0, 1); bodyContent.anchorMax = new Vector2(1, 1);
        bodyContent.pivot = new Vector2(0.5f, 1);
        bodyContent.sizeDelta = new Vector2(0, 0);
        fitter = bodyContent.GetComponent<ContentSizeFitter>() ?? bodyContent.gameObject.AddComponent<ContentSizeFitter>();
        fitter.verticalFit = ContentSizeFitter.FitMode.MinSize;
        scroll.content = bodyContent;

        BuildSection("now", out kvState, out kvTool, out kvElapsed, out kvLastDelta);
        BuildSection("current card", out kvCard, out kvCardStatus, out _, out _);
        stuckSec = BuildStuckSection(out stuckText);
        BuildStreamSection();
    }

    // ---------- public API ----------
    public void OpenAgent(string profile, OfficeHUD.AgentDto agent, OfficeHUD.OfficeStateDto dto)
    {
        if (root == null) Build(); // self-heal if Awake timing differs (batchmode)
        openProfile = profile;
        openAgent = agent;
        openDto = dto;
        Layout();
        Render();
        root.gameObject.SetActive(true);
        if (anim != null) StopCoroutine(anim);
        anim = StartCoroutine(AnimateIn());
    }

    public void Refresh(OfficeHUD.AgentDto agent, OfficeHUD.OfficeStateDto dto)
    {
        if (openProfile == null) return;
        openAgent = agent; openDto = dto;
        Render();
    }

    public void Close()
    {
        openProfile = null;
        openAgent = null;
        if (root != null) root.gameObject.SetActive(false);
    }

    public void PushStreamLine(string profile, string kind, string text, long tsMs)
    {
        if (!streams.TryGetValue(profile, out var list))
        {
            list = new List<StreamLine>();
            streams[profile] = list;
        }
        list.Add(new StreamLine { ts = tsMs, kind = kind, text = text });
        if (list.Count > 50) list.RemoveRange(0, list.Count - 50);
        if (openProfile == profile) RenderStream();
    }

    // ---------- layout ----------
    void Layout()
    {
        bool mobile = Screen.width <= 520;
        if (mobile)
        {
            root.anchorMin = new Vector2(0, 0); root.anchorMax = new Vector2(1, 0);
            root.pivot = new Vector2(0.5f, 0);
            float h = Mathf.Min(0.66f * Screen.height, 560f);
            root.sizeDelta = new Vector2(0, h);
            root.anchoredPosition = new Vector2(0, 0);
        }
        else
        {
            root.anchorMin = new Vector2(1, 0); root.anchorMax = new Vector2(1, 1);
            root.pivot = new Vector2(1, 0.5f);
            float w = Mathf.Min(380f, 0.44f * Screen.width);
            root.sizeDelta = new Vector2(w, 0);
            root.anchoredPosition = new Vector2(0, 0);
        }
        grab.gameObject.SetActive(mobile);
    }

    void OnClose() { Close(); }

    IEnumerator AnimateIn()
    {
        bool mobile = Screen.width <= 520;
        float dur = mobile ? 0.18f : 0.16f;
        var img = root.GetComponent<Image>();
        float t = 0f;
        Vector3 from = mobile ? new Vector3(0, -20, 0) : new Vector3(16, 0, 0);
        while (t < dur)
        {
            t += Time.unscaledDeltaTime;
            float k = Mathf.Clamp01(t / dur);
            root.anchoredPosition = Vector3.Lerp(from, root.anchoredPosition, k);
            img.color = new Color(PANEL.r, PANEL.g, PANEL.b, 0.4f + 0.57f * k);
            yield return null;
        }
        img.color = new Color(PANEL.r, PANEL.g, PANEL.b, 0.97f);
    }

    // ---------- render ----------
    void Render()
    {
        if (openAgent == null) return;
        stuck = openAgent.state != "done" && openDto.stuck_ms > 0 && openDto.now - openAgent.last_delta_ms > openDto.stuck_ms;
        title.text = openProfile;
        chip.text = stuck ? "stuck?" : (string.IsNullOrEmpty(openAgent.state) ? "idle" : openAgent.state);
        chip.color = stuck ? BAD : openAgent.state == "done" ? GOOD : openAgent.state == "thinking" ? BUSY : openAgent.state == "working" || openAgent.state == "typing" ? WORK : DIM;
        if (openAgent.state == null || openAgent.state == "idle")
        {
            kvState.text = "idle — no in-flight turn for this agent";
            kvTool.text = kvElapsed.text = kvLastDelta.text = "";
        }
        else
        {
            kvState.text = stuck ? "stuck?" : openAgent.state;
            kvState.color = stuck ? BAD : INK;
            kvTool.text = openAgent.tool_label ?? "";
            kvElapsed.text = OfficeHUD.FmtElapsed(openAgent.elapsed_ms);
            kvLastDelta.text = OfficeHUD.FmtAgo(openAgent.last_delta_ms, openDto.now);
        }
        if (!string.IsNullOrEmpty(openAgent.card_id))
        {
            kvCard.text = string.IsNullOrEmpty(openAgent.card_title) ? openAgent.card_id + " (not on board yet)" : openAgent.card_title;
            kvCardStatus.text = openAgent.card_state ?? "";
        }
        else
        {
            kvCard.text = "no card linked to this turn";
            kvCardStatus.text = "";
        }
        stuckSec.gameObject.SetActive(stuck);
        if (stuck)
            stuckText.text = "No live delta for " + OfficeHUD.FmtElapsed(openDto.now - openAgent.last_delta_ms) +
                             " while a turn is open (stuck threshold " + (openDto.stuck_ms / 1000) + "s)." +
                             (string.IsNullOrEmpty(openAgent.tool_label) ? "" : "\nLast tool: " + openAgent.tool_label) +
                             (string.IsNullOrEmpty(openAgent.card_title) ? "" : "\nOn card: " + openAgent.card_title);
        RenderStream();
    }

    void RenderStream()
    {
        var list = streams.TryGetValue(openProfile, out var l) ? l : null;
        int n = list != null ? list.Count : 0;
        streamEmpty.gameObject.SetActive(n == 0);
        for (int i = 0; i < streamLines.Length; i++)
        {
            bool on = i < n;
            streamLines[i].gameObject.SetActive(on);
            if (!on) continue;
            var sl = list[n - 1 - i]; // newest first
            string ts = new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc).AddMilliseconds(sl.ts).ToLocalTime().ToString("HH:mm:ss");
            streamLines[i].text = ts + "  " + sl.kind + "  " + sl.text;
            streamLines[i].color = sl.kind == "tool" ? WORK : sl.kind == "reasoning" ? BUSY : sl.kind == "text" ? TYPE : INK;
        }
    }

    // ---------- section builders ----------
    RectTransform cur;
    int yCursor = 0;

    void BuildSection(string heading, out Text kv1, out Text kv2, out Text kv3, out Text kv4)
    {
        var sec = new GameObject("sec_" + heading).AddComponent<RectTransform>();
        sec.SetParent(bodyContent, false);
        sec.anchorMin = new Vector2(0, 1); sec.anchorMax = new Vector2(1, 1);
        sec.pivot = new Vector2(0.5f, 1);
        sec.anchoredPosition = new Vector2(0, -yCursor);
        yCursor += 24;
        var h = OfficeHUD.MakeTextPublic("h3", heading.ToUpper(), 10, FontStyle.Bold, DIM);
        h.rectTransform.SetParent(sec, false);
        h.alignment = TextAnchor.UpperLeft;
        h.rectTransform.anchorMin = Vector2.zero; h.rectTransform.anchorMax = new Vector2(1, 1);
        h.rectTransform.offsetMin = Vector3.zero; h.rectTransform.offsetMax = new Vector3(0, -20);
        kv1 = MakeKv(sec, "state", yCursor += 20);
        kv2 = MakeKv(sec, "tool", yCursor += 20);
        kv3 = MakeKv(sec, "elapsed", yCursor += 20);
        kv4 = MakeKv(sec, "last delta", yCursor += 20);
        yCursor += 14;
    }

    Text MakeKv(RectTransform parent, string key, int y)
    {
        var row = new GameObject("kv_" + key).AddComponent<RectTransform>();
        row.SetParent(parent, false);
        row.anchorMin = new Vector2(0, 1); row.anchorMax = new Vector2(1, 1);
        row.pivot = new Vector2(0, 1);
        row.anchoredPosition = new Vector2(0, -y);
        row.sizeDelta = new Vector2(0, 18);
        var k = OfficeHUD.MakeTextPublic("k", key, 12, FontStyle.Normal, DIM);
        k.rectTransform.SetParent(row, false);
        k.alignment = TextAnchor.MiddleLeft;
        k.rectTransform.anchorMin = new Vector2(0, 0.5f); k.rectTransform.anchorMax = new Vector2(0, 0.5f);
        k.rectTransform.pivot = new Vector2(0, 0.5f);
        k.rectTransform.anchoredPosition = Vector2.zero;
        k.rectTransform.sizeDelta = new Vector2(96, 18);
        var v = OfficeHUD.MakeTextPublic("v", "", 12, FontStyle.Normal, INK);
        v.rectTransform.SetParent(row, false);
        v.alignment = TextAnchor.MiddleLeft;
        v.rectTransform.anchorMin = new Vector2(0, 0.5f); v.rectTransform.anchorMax = new Vector2(1, 0.5f);
        v.rectTransform.pivot = new Vector2(0, 0.5f);
        v.rectTransform.anchoredPosition = new Vector2(96, 0);
        v.rectTransform.sizeDelta = new Vector2(-96, 18);
        return v;
    }

    RectTransform BuildStuckSection(out Text body)
    {
        var sec = new GameObject("sec_stuck").AddComponent<RectTransform>();
        sec.SetParent(bodyContent, false);
        sec.anchorMin = new Vector2(0, 1); sec.anchorMax = new Vector2(1, 1);
        sec.pivot = new Vector2(0.5f, 1);
        sec.anchoredPosition = new Vector2(0, -yCursor);
        var h = OfficeHUD.MakeTextPublic("h3", "WHY STUCK", 10, FontStyle.Bold, BAD);
        h.rectTransform.SetParent(sec, false);
        h.alignment = TextAnchor.UpperLeft;
        h.rectTransform.anchorMin = Vector2.zero; h.rectTransform.anchorMax = new Vector2(1, 1);
        h.rectTransform.offsetMin = Vector3.zero; h.rectTransform.offsetMax = new Vector3(0, -20);
        body = OfficeHUD.MakeTextPublic("body", "", 12, FontStyle.Normal, INK);
        body.rectTransform.SetParent(sec, false);
        body.alignment = TextAnchor.UpperLeft;
        body.supportRichText = false;
        body.rectTransform.anchorMin = new Vector2(0, 1); body.rectTransform.anchorMax = new Vector2(1, 1);
        body.rectTransform.pivot = new Vector2(0, 1);
        body.rectTransform.anchoredPosition = new Vector2(0, -24);
        body.rectTransform.sizeDelta = new Vector2(0, 60);
        yCursor += 90;
        sec.gameObject.SetActive(false);
        return sec;
    }

    void BuildStreamSection()
    {
        var sec = new GameObject("sec_stream").AddComponent<RectTransform>();
        sec.SetParent(bodyContent, false);
        sec.anchorMin = new Vector2(0, 1); sec.anchorMax = new Vector2(1, 1);
        sec.pivot = new Vector2(0.5f, 1);
        sec.anchoredPosition = new Vector2(0, -yCursor);
        var h = OfficeHUD.MakeTextPublic("h3", "LIVE TURN STREAM", 10, FontStyle.Bold, DIM);
        h.rectTransform.SetParent(sec, false);
        h.alignment = TextAnchor.UpperLeft;
        h.rectTransform.anchorMin = Vector2.zero; h.rectTransform.anchorMax = new Vector2(1, 1);
        h.rectTransform.offsetMin = Vector3.zero; h.rectTransform.offsetMax = new Vector3(0, -20);
        streamEmpty = OfficeHUD.MakeTextPublic("empty", "no deltas yet this turn — stream is live", 12, FontStyle.Normal, DIM);
        streamEmpty.rectTransform.SetParent(sec, false);
        streamEmpty.alignment = TextAnchor.UpperLeft;
        streamEmpty.rectTransform.anchorMin = new Vector2(0, 1); streamEmpty.rectTransform.anchorMax = new Vector2(1, 1);
        streamEmpty.rectTransform.pivot = new Vector2(0, 1);
        streamEmpty.rectTransform.anchoredPosition = new Vector2(0, -24);
        streamEmpty.rectTransform.sizeDelta = new Vector2(0, 18);
        for (int i = 0; i < streamLines.Length; i++)
        {
            streamLines[i] = OfficeHUD.MakeTextPublic("line" + i, "", 11, FontStyle.Normal, INK);
            streamLines[i].rectTransform.SetParent(sec, false);
            streamLines[i].alignment = TextAnchor.UpperLeft;
            streamLines[i].horizontalOverflow = HorizontalWrapMode.Overflow;
            streamLines[i].rectTransform.anchorMin = new Vector2(0, 1); streamLines[i].rectTransform.anchorMax = new Vector2(1, 1);
            streamLines[i].rectTransform.pivot = new Vector2(0, 1);
            streamLines[i].rectTransform.anchoredPosition = new Vector2(0, -44 - i * 16);
            streamLines[i].rectTransform.sizeDelta = new Vector2(0, 16);
            streamLines[i].gameObject.SetActive(false);
        }
        yCursor += 24 + streamLines.Length * 16 + 14;
    }
}
