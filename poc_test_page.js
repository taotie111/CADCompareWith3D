/* global Cesium */

const state = {
  viewer: null,
  pollTimer: null,
  currentJobId: null,
  result: null,
  eventEntities: [],
  gatewayBaseUrl: "",
  bearerToken: "",
};

const el = {
  gatewayBaseUrl: document.getElementById("gatewayBaseUrl"),
  bearerToken: document.getElementById("bearerToken"),
  sceneMode: document.getElementById("sceneMode"),
  sceneUrl: document.getElementById("sceneUrl"),
  coordinateSystem: document.getElementById("coordinateSystem"),
  cadFile: document.getElementById("cadFile"),
  llmEnabled: document.getElementById("llmEnabled"),
  preferDwg: document.getElementById("preferDwg"),
  btnStart: document.getElementById("btnStart"),
  btnStopPoll: document.getElementById("btnStopPoll"),
  jobId: document.getElementById("jobId"),
  jobStatus: document.getElementById("jobStatus"),
  logBox: document.getElementById("logBox"),
  tabCesium: document.getElementById("tabCesium"),
  tabWeb: document.getElementById("tabWeb"),
  cesiumContainer: document.getElementById("cesiumContainer"),
  webFrame: document.getElementById("webFrame"),
  summary: document.getElementById("summary"),
  riskFilter: document.getElementById("riskFilter"),
  eventList: document.getElementById("eventList"),
};

function log(msg, isError = false) {
  const time = new Date().toLocaleTimeString();
  el.logBox.textContent += `[${time}] ${msg}\n`;
  el.logBox.scrollTop = el.logBox.scrollHeight;
  if (isError) {
    console.error(msg);
  }
}

function initCesium() {
  state.viewer = new Cesium.Viewer("cesiumContainer", {
    timeline: false,
    animation: false,
    baseLayerPicker: true,
    geocoder: false,
    homeButton: true,
    sceneModePicker: true,
    navigationHelpButton: false,
  });
  state.viewer.scene.globe.depthTestAgainstTerrain = true;
}

function switchViewer(mode) {
  if (mode === "cesium") {
    el.tabCesium.classList.add("active");
    el.tabWeb.classList.remove("active");
    el.cesiumContainer.classList.remove("hidden");
    el.webFrame.classList.add("hidden");
  } else {
    el.tabWeb.classList.add("active");
    el.tabCesium.classList.remove("active");
    el.webFrame.classList.remove("hidden");
    el.cesiumContainer.classList.add("hidden");
  }
}

async function api(path, options = {}) {
  const base = state.gatewayBaseUrl.replace(/\/$/, "");
  const url = `${base}${path}`;
  const headers = {
    ...(options.headers || {}),
  };

  if (state.bearerToken) {
    headers.Authorization = `Bearer ${state.bearerToken}`;
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status}: ${text}`);
  }

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response.text();
}

async function uploadCadFile() {
  const file = el.cadFile.files?.[0];
  if (!file) {
    throw new Error("请先选择DWG文件");
  }

  const formData = new FormData();
  formData.append("file", file);

  log(`上传CAD文件: ${file.name}`);
  const res = await api("/test/ingest/cad/upload", {
    method: "POST",
    body: formData,
  });

  const token = res?.data?.file_token || res?.file_token;
  if (!token) {
    throw new Error("上传成功但未返回 file_token");
  }
  return token;
}

function buildStartPayload(cadFileToken) {
  const sceneMode = el.sceneMode.value;
  const sceneUrl = el.sceneUrl.value.trim();

  return {
    scene_mode: sceneMode,
    scene_url: sceneUrl,
    tileset_url: sceneMode === "tileset_url" ? sceneUrl : "",
    coordinate_system: el.coordinateSystem.value,
    cad_file_token: cadFileToken,
    input_policy: {
      prefer_source: el.preferDwg.checked ? "dwg" : "merge",
      pdf_mode: "disabled",
    },
    llm_enabled: el.llmEnabled.checked,
  };
}

async function startJob() {
  state.gatewayBaseUrl = el.gatewayBaseUrl.value.trim();
  state.bearerToken = el.bearerToken.value.trim();

  if (!state.gatewayBaseUrl) {
    alert("请填写网关Base URL");
    return;
  }
  if (!el.sceneUrl.value.trim()) {
    alert("请填写场景地址");
    return;
  }

  try {
    const cadFileToken = await uploadCadFile();
    const payload = buildStartPayload(cadFileToken);

    log(`提交任务: ${JSON.stringify(payload)}`);
    const res = await api("/test/compare/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const jobId = res?.data?.job_id || res?.job_id;
    if (!jobId) {
      throw new Error("任务启动成功但未返回 job_id");
    }

    state.currentJobId = jobId;
    el.jobId.textContent = jobId;
    setStatus("queued");
    log(`任务已创建: ${jobId}`);

    if (el.sceneMode.value === "web_url") {
      el.webFrame.src = el.sceneUrl.value.trim();
      switchViewer("web");
    } else {
      switchViewer("cesium");
    }

    startPolling();
  } catch (err) {
    log(`启动任务失败: ${err.message}`, true);
    alert(`启动任务失败: ${err.message}`);
  }
}

function setStatus(status) {
  el.jobStatus.textContent = status;
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
    log("已停止轮询");
  }
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(async () => {
    if (!state.currentJobId) return;
    try {
      const job = await api(`/test/jobs/${state.currentJobId}`);
      const status = job?.data?.status || job?.status || "unknown";
      setStatus(status);
      if (["done", "failed", "cancelled"].includes(status)) {
        stopPolling();
        if (status === "done") {
          await fetchResult();
        }
      }
    } catch (err) {
      log(`轮询失败: ${err.message}`, true);
    }
  }, 2000);
  log("开始轮询任务状态");
}

async function fetchResult() {
  if (!state.currentJobId) return;
  try {
    const res = await api(`/test/jobs/${state.currentJobId}/result`);
    state.result = res?.data || res;
    renderSummary();
    renderEvents();
    renderSceneOverlay();
    log("结果加载完成");
  } catch (err) {
    log(`获取结果失败: ${err.message}`, true);
  }
}

function renderSummary() {
  const s = state.result?.summary;
  if (!s) {
    el.summary.textContent = "暂无结果";
    return;
  }

  el.summary.innerHTML = [
    `设计对象: ${s.design_objects ?? "-"}`,
    `实景对象: ${s.reality_objects ?? "-"}`,
    `匹配对象: ${s.matched_objects ?? "-"}`,
    `漏建: ${s.missing_objects ?? "-"}`,
    `疑似违建: ${s.unplanned_objects ?? "-"}`,
    `事件总数: ${s.events_total ?? "-"}`,
    `需人工复核: ${s.manual_review_required ?? "-"}`,
  ].join("<br>");
}

function levelBadge(level) {
  return `<span class=\"badge ${level}\">${level}</span>`;
}

function renderEvents() {
  const allEvents = state.result?.events || [];
  const filter = el.riskFilter.value;
  const events = filter === "all" ? allEvents : allEvents.filter((x) => x.level === filter);

  el.eventList.innerHTML = "";
  events.forEach((event, index) => {
    const li = document.createElement("li");
    li.className = "event-item";
    li.innerHTML = `
      <div><strong>${event.risk_type || event.rule_id || "事件"}</strong>${levelBadge(event.level || "low")}</div>
      <div>位置: ${event.location || "-"}</div>
      <div>建议: ${typeof event.suggestion === "string" ? event.suggestion : "详见详情"}</div>
      <div>来源: ${event.source_type || "-"} / 置信度: ${event.source_confidence ?? "-"}</div>
    `;

    li.addEventListener("click", () => flyToEvent(event, index));
    el.eventList.appendChild(li);
  });
}

function clearEventEntities() {
  if (!state.viewer) return;
  state.eventEntities.forEach((entity) => state.viewer.entities.remove(entity));
  state.eventEntities = [];
}

function renderSceneOverlay() {
  if (!state.viewer || el.sceneMode.value !== "tileset_url") return;

  clearEventEntities();

  const sceneUrl = el.sceneUrl.value.trim();
  if (sceneUrl) {
    loadTileset(sceneUrl);
  }

  const events = state.result?.events || [];
  events.forEach((event) => {
    const evidence = event.evidence || {};
    const x = evidence?.x ?? null;
    const y = evidence?.y ?? null;
    const z = evidence?.z ?? 0;

    if (x == null || y == null) {
      return;
    }

    const color = levelToColor(event.level);
    const entity = state.viewer.entities.add({
      position: Cesium.Cartesian3.fromDegrees(Number(x), Number(y), Number(z)),
      point: {
        pixelSize: 10,
        color,
        outlineColor: Cesium.Color.WHITE,
        outlineWidth: 1,
      },
      label: {
        text: `${event.level}: ${event.risk_type}`,
        font: "12px sans-serif",
        fillColor: Cesium.Color.WHITE,
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 2,
        showBackground: true,
        backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
        pixelOffset: new Cesium.Cartesian2(0, -20),
      },
      properties: event,
    });
    state.eventEntities.push(entity);
  });
}

async function loadTileset(url) {
  try {
    const tileset = await Cesium.Cesium3DTileset.fromUrl(url);
    state.viewer.scene.primitives.add(tileset);
    await state.viewer.zoomTo(tileset);
    log("Tileset加载成功");
  } catch (err) {
    log(`Tileset加载失败: ${err.message}`, true);
  }
}

function levelToColor(level) {
  if (level === "critical") return Cesium.Color.RED;
  if (level === "high") return Cesium.Color.ORANGE;
  if (level === "medium") return Cesium.Color.DODGERBLUE;
  return Cesium.Color.LIME;
}

function flyToEvent(event) {
  if (!state.viewer || el.sceneMode.value !== "tileset_url") return;

  const ev = event.evidence || {};
  const x = ev?.x;
  const y = ev?.y;
  const z = ev?.z ?? 50;
  if (x == null || y == null) {
    log("该事件缺少坐标，无法定位");
    return;
  }

  state.viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(Number(x), Number(y), Number(z) + 120),
    duration: 1.5,
  });
}

function bindEvents() {
  el.btnStart.addEventListener("click", startJob);
  el.btnStopPoll.addEventListener("click", stopPolling);
  el.riskFilter.addEventListener("change", renderEvents);

  el.tabCesium.addEventListener("click", () => switchViewer("cesium"));
  el.tabWeb.addEventListener("click", () => switchViewer("web"));
}

function bootstrap() {
  initCesium();
  bindEvents();
  log("页面初始化完成");
  log("提示：本页不直连核心服务，仅调用测试网关。\n");
}

bootstrap();
