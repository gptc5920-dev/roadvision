(function () {
  const payload = document.getElementById("detection-events");
  const scene = document.querySelector(".road-scene");

  const searchForm = document.getElementById("global-search");
  const searchInput = document.getElementById("global-search-input");
  const adminShell = document.querySelector(".admin-shell");
  const sidebar = document.getElementById("app-sidebar");
  const sidebarToggle = document.getElementById("sidebar-toggle");
  const sidebarMobileClose = document.getElementById("sidebar-mobile-close");
  const sidebarBackdrop = document.getElementById("sidebar-backdrop");
  const analyzeForm = document.getElementById("analyze-form");
  const uploadInput = document.querySelector('input[type="file"][name="video"]');
  const uploadLabel = document.getElementById("upload-label");
  const datasetSelect = document.querySelector("select[name='dataset_sample']");
  const formConfidence = document.querySelector("#analyze-form input[name='min_confidence']");
  const previewConfidence = document.getElementById("confidence-preview");
  const previewOutput = document.getElementById("confidence-preview-output");
  const clearForm = document.getElementById("clear-form");
  const openImageViewer = document.getElementById("open-image-viewer");
  const closeImageViewer = document.getElementById("close-image-viewer");
  const imageViewer = document.getElementById("image-viewer");
  const imageViewerTitle = document.getElementById("image-viewer-title");
  const imageViewerSubtitle = document.getElementById("image-viewer-subtitle");
  const imageViewerSingle = document.getElementById("image-viewer-single");
  const imageViewerSingleImg = document.getElementById("image-viewer-single-img");
  const imageViewerPrev = document.getElementById("image-viewer-prev");
  const imageViewerNext = document.getElementById("image-viewer-next");
  const imageViewerGrid = document.querySelector(".image-viewer-grid");
  const defaultImageViewerSubtitle = imageViewerSubtitle?.textContent || "";
  const imageViewerItems = Array.from(document.querySelectorAll("[data-gallery-image]")).map((item) => ({
    src: item.dataset.galleryImage,
    title: item.dataset.galleryTitle || item.textContent?.trim() || "Detected pavement defect snapshot",
  }));
  let imageViewerIndex = -1;

  const mobileNavigation = window.matchMedia("(max-width: 980px)");
  document.body.classList.add("responsive-nav-ready");

  function setSidebarButton(icon, label, expanded) {
    if (!sidebarToggle) return;
    sidebarToggle.textContent = mobileNavigation.matches ? `${icon} Menu` : icon;
    sidebarToggle.setAttribute("aria-label", label);
    sidebarToggle.setAttribute("aria-expanded", String(expanded));
    sidebarToggle.setAttribute("aria-controls", "app-sidebar");
  }

  function setMobileNavigation(open, moveFocus = false) {
    if (!adminShell || !sidebarToggle) return;
    adminShell.classList.toggle("mobile-nav-open", open);
    document.body.classList.toggle("mobile-nav-open", open);
    if (sidebar) sidebar.inert = !open;
    setSidebarButton("\u2630", open ? "Close navigation" : "Open navigation", open);
    if (open && moveFocus) sidebarMobileClose?.focus();
  }

  function syncSidebarForViewport() {
    if (!adminShell || !sidebarToggle) return;
    if (mobileNavigation.matches) {
      adminShell.classList.remove("sidebar-collapsed");
      setMobileNavigation(false);
      return;
    }
    document.body.classList.remove("mobile-nav-open");
    adminShell.classList.remove("mobile-nav-open");
    if (sidebar) sidebar.inert = false;
    const collapsed = localStorage.getItem("roadvision-sidebar-collapsed") === "true";
    adminShell.classList.toggle("sidebar-collapsed", collapsed);
    setSidebarButton(collapsed ? "\u203a" : "\u2630", collapsed ? "Expand navigation" : "Collapse navigation", !collapsed);
  }

  syncSidebarForViewport();
  mobileNavigation.addEventListener?.("change", syncSidebarForViewport);

  sidebarToggle?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!adminShell) return;
    if (mobileNavigation.matches) {
      setMobileNavigation(!adminShell.classList.contains("mobile-nav-open"), true);
      return;
    }
    const collapsed = !adminShell.classList.contains("sidebar-collapsed");
    adminShell.classList.toggle("sidebar-collapsed", collapsed);
    setSidebarButton(collapsed ? "\u203a" : "\u2630", collapsed ? "Expand navigation" : "Collapse navigation", !collapsed);
    localStorage.setItem("roadvision-sidebar-collapsed", String(collapsed));
  }, true);

  function closeMobileNavigation() {
    if (mobileNavigation.matches) setMobileNavigation(false);
  }

  sidebarMobileClose?.addEventListener("click", () => {
    closeMobileNavigation();
    sidebarToggle?.focus();
  });
  sidebarBackdrop?.addEventListener("click", () => {
    closeMobileNavigation();
    sidebarToggle?.focus();
  });
  sidebar?.querySelectorAll("nav a, .ops-brand, .sidebar-user a").forEach((link) => {
    link.addEventListener("click", closeMobileNavigation);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && adminShell?.classList.contains("mobile-nav-open")) {
      closeMobileNavigation();
      sidebarToggle?.focus();
    }
  });

  document.querySelectorAll(".table-wrap").forEach((wrapper) => {
    if (!wrapper.hasAttribute("tabindex")) wrapper.tabIndex = 0;
    if (!wrapper.hasAttribute("aria-label")) wrapper.setAttribute("aria-label", "Scrollable data table");
    if (!wrapper.previousElementSibling?.classList.contains("table-scroll-hint")) {
      const hint = document.createElement("p");
      hint.className = "table-scroll-hint";
      hint.textContent = "Swipe left or right to view all table columns.";
      wrapper.before(hint);
    }
  });

  searchForm?.addEventListener("submit", (event) => event.preventDefault());

  function applyGlobalSearch(query) {
    const normalized = query.trim().toLowerCase();
    const searchable = Array.from(document.querySelectorAll("[data-search-text]"));
    searchable.forEach((item) => {
      const text = (item.dataset.searchText || item.textContent || "").toLowerCase();
      item.hidden = normalized.length > 0 && !text.includes(normalized);
    });
  }

  searchInput?.addEventListener("input", () => applyGlobalSearch(searchInput.value));

  uploadInput?.addEventListener("change", () => {
    const file = uploadInput.files && uploadInput.files[0];
    if (uploadLabel) {
      uploadLabel.childNodes[0].nodeValue = file ? "Video selected" : "Upload and detect";
    }
    if (datasetSelect && file) datasetSelect.value = "";
  });

  function openImageViewerModal() {
    if (!imageViewer) return;
    imageViewer.hidden = false;
    document.body.classList.add("modal-open");
  }

  function closeImageViewerModal() {
    if (!imageViewer) return;
    imageViewer.hidden = true;
    document.body.classList.remove("modal-open");
  }

  function showImageGallery() {
    imageViewerIndex = -1;
    if (imageViewerTitle) imageViewerTitle.textContent = "Defect documentation images";
    if (imageViewerSubtitle) {
      imageViewerSubtitle.hidden = false;
      imageViewerSubtitle.textContent = defaultImageViewerSubtitle;
    }
    if (imageViewerSingle) imageViewerSingle.hidden = true;
    if (imageViewerSingleImg) imageViewerSingleImg.removeAttribute("src");
    if (imageViewerGrid) imageViewerGrid.hidden = false;
    openImageViewerModal();
  }

  function updateImageViewerControls() {
    const disabled = imageViewerItems.length <= 1 || imageViewerIndex < 0;
    if (imageViewerPrev) imageViewerPrev.disabled = disabled;
    if (imageViewerNext) imageViewerNext.disabled = disabled;
  }

  function showSingleImage(src, title, index = -1) {
    if (!src) return;
    imageViewerIndex = index >= 0 ? index : imageViewerItems.findIndex((item) => item.src === src);
    if (imageViewerTitle) imageViewerTitle.textContent = "Defect documentation image";
    if (imageViewerSubtitle) {
      imageViewerSubtitle.hidden = false;
      const position = imageViewerIndex >= 0 ? ` (${imageViewerIndex + 1}/${imageViewerItems.length})` : "";
      imageViewerSubtitle.textContent = `${title || "Captured detection snapshot"}${position}`;
    }
    if (imageViewerGrid) imageViewerGrid.hidden = true;
    if (imageViewerSingleImg) {
      imageViewerSingleImg.src = src;
      imageViewerSingleImg.alt = title || "Detected pavement defect snapshot";
    }
    if (imageViewerSingle) imageViewerSingle.hidden = false;
    updateImageViewerControls();
    openImageViewerModal();
  }

  function showImageAtIndex(index) {
    if (!imageViewerItems.length) return;
    const nextIndex = (index + imageViewerItems.length) % imageViewerItems.length;
    const item = imageViewerItems[nextIndex];
    showSingleImage(item.src, item.title, nextIndex);
  }

  openImageViewer?.addEventListener("click", () => {
    showImageGallery();
  });
  closeImageViewer?.addEventListener("click", () => {
    closeImageViewerModal();
  });
  imageViewer?.addEventListener("click", (event) => {
    if (event.target !== imageViewer) return;
    closeImageViewerModal();
  });
  document.addEventListener("keydown", (event) => {
    if (!imageViewer || imageViewer.hidden) return;
    if (event.key === "Escape") {
      closeImageViewerModal();
    } else if (event.key === "ArrowLeft" && imageViewerSingle && !imageViewerSingle.hidden) {
      event.preventDefault();
      showImageAtIndex(imageViewerIndex - 1);
    } else if (event.key === "ArrowRight" && imageViewerSingle && !imageViewerSingle.hidden) {
      event.preventDefault();
      showImageAtIndex(imageViewerIndex + 1);
    }
  });
  document.querySelectorAll("[data-view-image]").forEach((button, index) => {
    button.addEventListener("click", () => {
      showSingleImage(button.dataset.viewImage, button.dataset.viewTitle, index);
    });
  });
  document.querySelectorAll("[data-gallery-image]").forEach((tile, index) => {
    tile.addEventListener("click", (event) => {
      event.preventDefault();
      showSingleImage(tile.dataset.galleryImage, tile.dataset.galleryTitle, index);
    });
  });
  imageViewerPrev?.addEventListener("click", () => showImageAtIndex(imageViewerIndex - 1));
  imageViewerNext?.addEventListener("click", () => showImageAtIndex(imageViewerIndex + 1));

  clearForm?.addEventListener("submit", (event) => {
    if (!confirm("Clear all video analysis runs and detection events?")) {
      event.preventDefault();
    }
  });

  document.querySelectorAll(".restart-analysis-form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const label = form.dataset.analysisLabel || "this video";
      const confirmed = confirm(
        `Restart pothole analysis for ${label}? Current detections, masks, snapshots, and review results for this run will be replaced. The original video will be kept.`
      );
      if (!confirmed) event.preventDefault();
    });
  });

  function syncConfidence(value) {
    if (formConfidence) formConfidence.value = value;
    if (previewConfidence) previewConfidence.value = value;
    if (previewOutput) previewOutput.textContent = `${value}%`;
  }

  previewConfidence?.addEventListener("input", () => syncConfidence(previewConfidence.value));
  formConfidence?.addEventListener("input", () => syncConfidence(formConfidence.value));

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[character]);
  }

  function initLiveMap() {
    const mapNode = document.getElementById("live-map-canvas");
    const reportsPayload = document.getElementById("live-map-reports");
    if (!mapNode || !reportsPayload) return;

    if (!window.L) {
      mapNode.innerHTML = '<div class="map-empty">Map provider could not be loaded.</div>';
      return;
    }

    let reports = [];
    try {
      reports = JSON.parse(reportsPayload.textContent || "[]");
    } catch (error) {
      mapNode.innerHTML = '<div class="map-empty">Map reports could not be loaded.</div>';
      return;
    }

    const validReports = reports
      .map((report) => ({ ...report, lat: Number(report.lat), lng: Number(report.lng) }))
      .filter((report) => Number.isFinite(report.lat) && Number.isFinite(report.lng));
    const center = validReports.length ? [validReports[0].lat, validReports[0].lng] : [12.8797, 121.774];
    const map = window.L.map(mapNode, { scrollWheelZoom: true }).setView(center, validReports.length ? 11 : 6);
    const markerByReport = new Map();
    const reportById = new Map(validReports.map((report) => [String(report.id), report]));
    const bounds = [];
    const severityColors = {
      critical: "#ef4444",
      high: "#f97316",
      medium: "#f59e0b",
      low: "#14b8a6",
    };
    const routeForm = document.getElementById("map-directions-form");
    const routeStart = document.getElementById("route-start");
    const routeEnd = document.getElementById("route-end");
    const routeStatus = document.getElementById("route-status");
    const useCurrentLocation = document.getElementById("use-current-location");
    const clearRoute = document.getElementById("clear-route");
    let routeLayer = null;
    let routeEndpointLayer = null;

    window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);

    validReports.forEach((report) => {
      const color = severityColors[report.severity] || "#f97316";
      const marker = window.L.circleMarker([report.lat, report.lng], {
        radius: report.severity === "critical" ? 10 : 8,
        color,
        fillColor: color,
        fillOpacity: 1,
        weight: 2,
      }).addTo(map);

      marker.bindPopup(`
        <div class="map-popup">
          <strong>#${escapeHtml(report.id)} ${escapeHtml(report.city)}</strong>
          <span>${escapeHtml(report.device_id)} - ${escapeHtml(report.status)}</span>
          <span>${escapeHtml(report.severity)} severity</span>
          <p>${escapeHtml(report.notes)}</p>
        </div>
      `);
      markerByReport.set(String(report.id), marker);
      bounds.push([report.lat, report.lng]);
    });

    function setRouteStatus(message, state = "") {
      if (!routeStatus) return;
      routeStatus.textContent = message;
      routeStatus.dataset.state = state;
    }

    function parseCoordinateInput(value) {
      const match = value.trim().match(/^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$/);
      if (!match) return null;
      const lat = Number(match[1]);
      const lng = Number(match[2]);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
      if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
      return { lat, lng, label: `${lat.toFixed(6)}, ${lng.toFixed(6)}` };
    }

    async function resolvePlace(value) {
      const coordinate = parseCoordinateInput(value);
      if (coordinate) return coordinate;
      const response = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(value)}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("Location lookup failed.");
      const results = await response.json();
      if (!results.length) throw new Error(`No map match found for "${value}".`);
      return {
        lat: Number(results[0].lat),
        lng: Number(results[0].lon),
        label: results[0].display_name,
      };
    }

    function clearRouteLayers() {
      if (routeLayer) routeLayer.remove();
      if (routeEndpointLayer) routeEndpointLayer.remove();
      routeLayer = null;
      routeEndpointLayer = null;
    }

    async function drawRoute(start, end) {
      const routeUrl = [
        "https://router.project-osrm.org/route/v1/driving/",
        `${start.lng},${start.lat};${end.lng},${end.lat}`,
        "?overview=full&geometries=geojson&steps=false",
      ].join("");
      const response = await fetch(routeUrl, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Route service did not respond.");
      const data = await response.json();
      const route = data.routes && data.routes[0];
      if (!route) throw new Error("No drivable route was found.");

      clearRouteLayers();
      routeLayer = window.L.geoJSON(route.geometry, {
        style: {
          color: "#38bdf8",
          weight: 5,
        },
      }).addTo(map);
      routeEndpointLayer = window.L.layerGroup([
        window.L.circleMarker([start.lat, start.lng], {
          radius: 7,
          color: "#22c55e",
          fillColor: "#22c55e",
          fillOpacity: 1,
          weight: 2,
        }).bindPopup(`<strong>Start</strong><br>${escapeHtml(start.label)}`),
        window.L.circleMarker([end.lat, end.lng], {
          radius: 7,
          color: "#38bdf8",
          fillColor: "#38bdf8",
          fillOpacity: 1,
          weight: 2,
        }).bindPopup(`<strong>Destination</strong><br>${escapeHtml(end.label)}`),
      ]).addTo(map);
      map.fitBounds(routeLayer.getBounds(), { padding: [36, 36] });

      const kilometers = route.distance / 1000;
      const minutes = Math.round(route.duration / 60);
      setRouteStatus(`${kilometers.toFixed(1)} km route, about ${minutes} min.`, "ready");
    }

    if (bounds.length) {
      map.fitBounds(bounds, { padding: [34, 34], maxZoom: 14 });
    }

    document.querySelectorAll("[data-map-report-link]").forEach((row) => {
      row.addEventListener("click", () => {
        const marker = markerByReport.get(row.dataset.mapReportLink);
        const report = reportById.get(row.dataset.mapReportLink);
        document.querySelectorAll("[data-map-report-link].is-active").forEach((item) => item.classList.remove("is-active"));
        row.classList.add("is-active");
        window.setTimeout(() => row.classList.remove("is-active"), 1400);
        if (routeEnd && report) {
          routeEnd.value = `${report.lat.toFixed(6)}, ${report.lng.toFixed(6)}`;
          setRouteStatus(`Destination set to #${report.id} ${report.city}.`, "ready");
        }
        if (!marker) return;
        map.setView(marker.getLatLng(), Math.max(map.getZoom(), 15), { animate: true });
        marker.openPopup();
      });
    });

    document.querySelectorAll("[data-route-target]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        const report = reportById.get(button.dataset.routeTarget);
        const marker = markerByReport.get(button.dataset.routeTarget);
        if (!report || !routeEnd) return;
        routeEnd.value = `${report.lat.toFixed(6)}, ${report.lng.toFixed(6)}`;
        setRouteStatus(`Destination set to #${report.id} ${report.city}.`, "ready");
        marker?.openPopup();
        marker && map.setView(marker.getLatLng(), Math.max(map.getZoom(), 15), { animate: true });
      });
    });

    routeForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const startValue = routeStart?.value.trim() || "";
      const endValue = routeEnd?.value.trim() || "";
      if (!startValue || !endValue) {
        setRouteStatus("Enter both start and destination.", "error");
        return;
      }
      try {
        setRouteStatus("Finding route...", "loading");
        const [start, end] = await Promise.all([resolvePlace(startValue), resolvePlace(endValue)]);
        await drawRoute(start, end);
      } catch (error) {
        clearRouteLayers();
        setRouteStatus(error.message || "Route could not be created.", "error");
      }
    });

    useCurrentLocation?.addEventListener("click", () => {
      if (!navigator.geolocation || !routeStart) {
        setRouteStatus("Browser location is not available.", "error");
        return;
      }
      setRouteStatus("Reading current location...", "loading");
      navigator.geolocation.getCurrentPosition(
        (position) => {
          const lat = position.coords.latitude;
          const lng = position.coords.longitude;
          routeStart.value = `${lat.toFixed(6)}, ${lng.toFixed(6)}`;
          setRouteStatus("Start location set from browser location.", "ready");
        },
        () => setRouteStatus("Browser location permission was denied.", "error"),
        { enableHighAccuracy: true, timeout: 10000 }
      );
    });

    clearRoute?.addEventListener("click", () => {
      clearRouteLayers();
      if (routeStart) routeStart.value = "";
      if (routeEnd) routeEnd.value = "";
      setRouteStatus("Select an incident or enter a destination.");
    });

    window.setTimeout(() => map.invalidateSize(), 0);
  }

  initLiveMap();

  function initDatasetUpload() {
    const input = document.getElementById("dataset-image-input");
    const dropZone = document.getElementById("dataset-drop-zone");
    const previewGrid = document.getElementById("dataset-preview-grid");
    if (!input || !dropZone || !previewGrid) return;

    function renderPreviews(files) {
      previewGrid.innerHTML = "";
      Array.from(files || []).slice(0, 24).forEach((file) => {
        const tile = document.createElement("article");
        tile.className = "dataset-preview-tile";
        const image = document.createElement("img");
        image.alt = file.name;
        const label = document.createElement("span");
        label.textContent = `${file.name} - ${(file.size / 1024 / 1024).toFixed(2)} MB`;
        tile.append(image, label);
        previewGrid.appendChild(tile);
        const reader = new FileReader();
        reader.addEventListener("load", () => {
          image.src = reader.result;
        });
        reader.readAsDataURL(file);
      });
    }

    input.addEventListener("change", () => renderPreviews(input.files));
    ["dragenter", "dragover"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.add("is-dragging");
      });
    });
    ["dragleave", "drop"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.remove("is-dragging");
      });
    });
    dropZone.addEventListener("drop", (event) => {
      if (!event.dataTransfer?.files?.length) return;
      input.files = event.dataTransfer.files;
      renderPreviews(input.files);
    });
  }

  function initDatasetAnnotator() {
    const payloadNode = document.getElementById("dataset-annotations");
    const overlay = document.getElementById("annotation-overlay");
    const image = document.getElementById("annotation-image");
    const stage = document.getElementById("annotation-stage");
    const form = document.getElementById("annotation-form");
    const output = document.getElementById("annotations-json");
    const deleteSelected = document.getElementById("delete-selected-box");
    const clearBoxes = document.getElementById("clear-boxes");
    const startMask = document.getElementById("start-mask");
    const undoLastMask = document.getElementById("undo-last-mask");
    const drawStatus = document.getElementById("mask-draw-status");
    if (!payloadNode || !overlay || !image || !stage || !form || !output) return;

    let annotations = [];
    try {
      annotations = JSON.parse(payloadNode.textContent || "[]")
        .filter((item) => Array.isArray(item.segmentation_points) && item.segmentation_points.length >= 3);
    } catch (error) {
      annotations = [];
    }
    let selectedIndex = annotations.length ? 0 : -1;
    let drawingPoints = [];
    let drawingEnabled = false;
    let activePointerId = null;

    function setDrawStatus(message) {
      if (drawStatus) drawStatus.textContent = message;
    }

    function rect() {
      return overlay.getBoundingClientRect();
    }

    function syncOverlayToImage() {
      const stageRect = stage.getBoundingClientRect();
      const elementRect = image.getBoundingClientRect();
      const naturalRatio = image.naturalWidth && image.naturalHeight
        ? image.naturalWidth / image.naturalHeight
        : elementRect.width / Math.max(elementRect.height, 1);
      const elementRatio = elementRect.width / Math.max(elementRect.height, 1);
      let width = elementRect.width;
      let height = elementRect.height;
      let left = elementRect.left - stageRect.left;
      let top = elementRect.top - stageRect.top;
      if (elementRatio > naturalRatio) {
        width = height * naturalRatio;
        left += (elementRect.width - width) / 2;
      } else {
        height = width / naturalRatio;
        top += (elementRect.height - height) / 2;
      }
      Object.assign(overlay.style, {
        left: `${left}px`,
        top: `${top}px`,
        right: "auto",
        bottom: "auto",
        width: `${width}px`,
        height: `${height}px`,
      });
    }

    function pointFromEvent(event) {
      const bounds = rect();
      return [
        Number((Math.max(0, Math.min(bounds.width, event.clientX - bounds.left)) / Math.max(bounds.width, 1)).toFixed(6)),
        Number((Math.max(0, Math.min(bounds.height, event.clientY - bounds.top)) / Math.max(bounds.height, 1)).toFixed(6)),
      ];
    }

    function annotationFromPoints(points) {
      const xs = points.map((point) => Number(point[0]));
      const ys = points.map((point) => Number(point[1]));
      const left = Math.min(...xs);
      const right = Math.max(...xs);
      const top = Math.min(...ys);
      const bottom = Math.max(...ys);
      return {
        center_x: Number(((left + right) / 2).toFixed(6)),
        center_y: Number(((top + bottom) / 2).toFixed(6)),
        width: Number((right - left).toFixed(6)),
        height: Number((bottom - top).toFixed(6)),
        segmentation_points: points.map((point) => [Number(point[0]), Number(point[1])]),
      };
    }

    function polygonArea(points) {
      return Math.abs(points.reduce((area, point, index) => {
        const next = points[(index + 1) % points.length];
        return area + Number(point[0]) * Number(next[1]) - Number(next[0]) * Number(point[1]);
      }, 0)) / 2;
    }

    function pixelPoints(points) {
      const bounds = rect();
      return points.map((point) => [Number(point[0]) * bounds.width, Number(point[1]) * bounds.height]);
    }

    function serializedAnnotations() {
      return annotations
        .filter((annotation) => annotation.segmentation_points.length >= 3)
        .map((annotation) => annotationFromPoints(annotation.segmentation_points));
    }

    function updateToolbarState() {
      if (startMask) {
        startMask.classList.toggle("is-active", drawingEnabled);
        startMask.setAttribute("aria-pressed", String(drawingEnabled));
        startMask.textContent = drawingEnabled ? "Done drawing" : "Draw mask";
      }
      if (undoLastMask) undoLastMask.disabled = annotations.length === 0;
      if (deleteSelected) deleteSelected.disabled = selectedIndex < 0;
      if (clearBoxes) clearBoxes.disabled = annotations.length === 0 && drawingPoints.length === 0;
      overlay.classList.toggle("is-drawing", drawingEnabled);
    }

    function render() {
      overlay.innerHTML = "";
      const bounds = rect();
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.classList.add("annotation-mask-svg");
      svg.setAttribute("viewBox", `0 0 ${Math.max(bounds.width, 1)} ${Math.max(bounds.height, 1)}`);
      annotations.forEach((annotation, index) => {
        const polygon = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
        polygon.dataset.maskIndex = index;
        polygon.setAttribute("points", pixelPoints(annotation.segmentation_points).map((point) => point.join(",")).join(" "));
        polygon.setAttribute("class", `annotation-mask${index === selectedIndex ? " is-selected" : ""}`);
        svg.appendChild(polygon);
      });
      if (drawingPoints.length) {
        const draftPixels = pixelPoints(drawingPoints);
        const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
        line.setAttribute("points", draftPixels.map((point) => point.join(",")).join(" "));
        line.setAttribute("class", "annotation-mask is-draft");
        svg.appendChild(line);
        [draftPixels[0], draftPixels[draftPixels.length - 1]].filter(Boolean).forEach((point) => {
          const vertex = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          vertex.setAttribute("cx", point[0]);
          vertex.setAttribute("cy", point[1]);
          vertex.setAttribute("r", "4");
          vertex.setAttribute("class", "annotation-mask-vertex");
          svg.appendChild(vertex);
        });
      }
      overlay.appendChild(svg);
      output.value = JSON.stringify(serializedAnnotations());
      updateToolbarState();
    }

    function finishDrawing() {
      const points = drawingPoints;
      drawingPoints = [];
      activePointerId = null;
      if (points.length >= 3 && polygonArea(points) > 0.000001) {
        annotations.push(annotationFromPoints(points));
        selectedIndex = annotations.length - 1;
        setDrawStatus(`Mask ${annotations.length} placed. Draw another pothole or save masks to the dataset.`);
      } else if (points.length) {
        setDrawStatus("That stroke was too small. Drag a complete loop around the pothole.");
      }
      render();
    }

    function appendDrawingPoint(event, force = false) {
      const point = pointFromEvent(event);
      const previous = drawingPoints[drawingPoints.length - 1];
      const bounds = rect();
      const distance = previous
        ? Math.hypot((point[0] - previous[0]) * bounds.width, (point[1] - previous[1]) * bounds.height)
        : Number.POSITIVE_INFINITY;
      if (!force && distance < 4) return;
      drawingPoints.push(point);
      if (drawingPoints.length > 480) {
        const lastPoint = drawingPoints[drawingPoints.length - 1];
        drawingPoints = drawingPoints.filter((unused, index) => index % 2 === 0);
        if (drawingPoints[drawingPoints.length - 1] !== lastPoint) drawingPoints.push(lastPoint);
      }
    }

    function setDrawingEnabled(enabled) {
      drawingEnabled = enabled;
      if (!enabled && drawingPoints.length) {
        drawingPoints = [];
        activePointerId = null;
      }
      setDrawStatus(enabled
        ? "Drag around the pothole and release to place the mask."
        : "Select Draw mask to add another pothole mask.");
      render();
    }

    function undoLast() {
      if (!annotations.length) return;
      annotations.pop();
      selectedIndex = annotations.length ? annotations.length - 1 : -1;
      setDrawStatus(annotations.length ? `Removed the last mask. ${annotations.length} remain.` : "Removed the last mask.");
      render();
    }

    overlay.addEventListener("pointerdown", (event) => {
      if (!drawingEnabled) {
        const maskNode = event.target.closest?.("[data-mask-index]");
        if (!maskNode) return;
        selectedIndex = Number(maskNode.dataset.maskIndex);
        setDrawStatus(`Mask ${selectedIndex + 1} selected.`);
        render();
        return;
      }

      if (event.pointerType === "mouse" && event.button !== 0) return;
      event.preventDefault();
      overlay.focus({ preventScroll: true });
      activePointerId = event.pointerId;
      drawingPoints = [];
      appendDrawingPoint(event, true);
      try {
        overlay.setPointerCapture(event.pointerId);
      } catch (error) {
        // Pointer capture is optional; document-level pointer events still finish the stroke.
      }
      setDrawStatus("Drawing mask boundary... release to place it.");
      render();
    });

    overlay.addEventListener("pointermove", (event) => {
      if (activePointerId !== event.pointerId) return;
      event.preventDefault();
      appendDrawingPoint(event);
      render();
    });

    overlay.addEventListener("pointerup", (event) => {
      if (activePointerId !== event.pointerId) return;
      event.preventDefault();
      appendDrawingPoint(event, true);
      try {
        overlay.releasePointerCapture(event.pointerId);
      } catch (error) {
        // The pointer may already have been released by the browser.
      }
      finishDrawing();
    });

    overlay.addEventListener("pointercancel", (event) => {
      if (activePointerId !== event.pointerId) return;
      drawingPoints = [];
      activePointerId = null;
      setDrawStatus("Drawing cancelled. Drag again to place the mask.");
      render();
    });

    startMask?.addEventListener("click", () => {
      setDrawingEnabled(!drawingEnabled);
    });
    undoLastMask?.addEventListener("click", undoLast);

    deleteSelected?.addEventListener("click", () => {
      if (selectedIndex < 0) return;
      annotations.splice(selectedIndex, 1);
      selectedIndex = annotations.length ? Math.min(selectedIndex, annotations.length - 1) : -1;
      setDrawStatus(annotations.length ? `Selected mask deleted. ${annotations.length} remain.` : "Selected mask deleted.");
      render();
    });

    clearBoxes?.addEventListener("click", () => {
      annotations = [];
      drawingPoints = [];
      activePointerId = null;
      selectedIndex = -1;
      setDrawStatus("All masks cleared. Draw a new mask or save an empty annotation set.");
      render();
    });

    overlay.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        undoLast();
      } else if (event.key === "Escape") {
        event.preventDefault();
        setDrawingEnabled(false);
      } else if ((event.key === "Delete" || event.key === "Backspace") && selectedIndex >= 0 && !drawingEnabled) {
        event.preventDefault();
        deleteSelected?.click();
      }
    });

    form.addEventListener("submit", () => {
      if (drawingPoints.length) finishDrawing();
      output.value = JSON.stringify(serializedAnnotations());
    });

    function syncAndRender() {
      syncOverlayToImage();
      render();
    }
    image.addEventListener("load", syncAndRender);
    window.addEventListener("resize", syncAndRender);
    syncAndRender();
  }

  initDatasetUpload();
  initDatasetAnnotator();

  function inferredFacingMode(label, fallback = "environment") {
    const normalized = String(label || "").toLowerCase();
    if (/(front|user|facetime|selfie)/.test(normalized)) return "user";
    if (/(back|rear|environment|world)/.test(normalized)) return "environment";
    return fallback;
  }

  function updateFacingControl(preview, flipButton, facingMode) {
    const isFrontCamera = facingMode === "user";
    preview.classList.toggle("is-user-facing", isFrontCamera);
    flipButton.textContent = isFrontCamera ? "Use back camera" : "Use front camera";
    flipButton.setAttribute(
      "aria-label",
      isFrontCamera ? "Switch to back camera" : "Switch to front camera"
    );
  }

  function initWebcamRecorder() {
    const preview = document.getElementById("webcam-preview");
    const startButton = document.getElementById("webcam-start");
    const flipButton = document.getElementById("webcam-flip");
    const recordButton = document.getElementById("webcam-record");
    const stopButton = document.getElementById("webcam-stop");
    const status = document.getElementById("webcam-status");
    const fileInput = document.getElementById("id_video");
    if (!preview || !startButton || !flipButton || !recordButton || !stopButton || !fileInput) return;

    let stream = null;
    let recorder = null;
    let chunks = [];
    let currentFacingMode = "environment";

    function setStatus(message) {
      if (status) status.textContent = message;
    }

    function releaseCamera() {
      stream?.getTracks().forEach((track) => track.stop());
      stream = null;
      preview.pause();
      preview.srcObject = null;
      preview.hidden = true;
      preview.classList.remove("is-user-facing");
      flipButton.disabled = true;
    }

    async function openCamera(facingMode = currentFacingMode, deviceId = "") {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
        setStatus("Camera recording is not supported by this browser. Use a current browser over HTTPS.");
        return false;
      }

      releaseCamera();
      startButton.disabled = true;
      recordButton.disabled = true;
      stopButton.disabled = true;
      setStatus(`Opening the ${facingMode === "user" ? "front" : "back"} camera...`);
      try {
        const videoConstraints = {
          width: { ideal: 1280 },
          height: { ideal: 720 },
        };
        if (deviceId) {
          videoConstraints.deviceId = { exact: deviceId };
        } else {
          videoConstraints.facingMode = { ideal: facingMode };
        }
        stream = await navigator.mediaDevices.getUserMedia({
          video: videoConstraints,
          audio: false,
        });
        preview.srcObject = stream;
        preview.hidden = false;
        await preview.play();
        const settings = stream.getVideoTracks()[0]?.getSettings?.() || {};
        currentFacingMode = settings.facingMode || facingMode;
        updateFacingControl(preview, flipButton, currentFacingMode);
        recordButton.disabled = false;
        flipButton.disabled = false;
        stopButton.disabled = false;
        startButton.disabled = true;
        setStatus(
          `${currentFacingMode === "user" ? "Front" : "Back"} camera ready. Record a road survey clip, then queue analysis.`
        );
        return true;
      } catch (error) {
        releaseCamera();
        startButton.disabled = false;
        recordButton.disabled = true;
        stopButton.disabled = true;
        setStatus("Camera access is unavailable. Check browser permission and use HTTPS.");
        return false;
      }
    }

    startButton.addEventListener("click", () => {
      openCamera(currentFacingMode);
    });

    flipButton.addEventListener("click", async () => {
      if (!stream || (recorder && recorder.state !== "inactive")) return;
      const previousFacingMode = currentFacingMode;
      const previousDeviceId = stream.getVideoTracks()[0]?.getSettings?.().deviceId || "";
      const requestedFacingMode = currentFacingMode === "user" ? "environment" : "user";
      const opened = await openCamera(requestedFacingMode);
      if (!opened) {
        await openCamera(previousFacingMode);
        setStatus("That camera could not be opened. The previous camera is active.");
        return;
      }

      const openedDeviceId = stream?.getVideoTracks()[0]?.getSettings?.().deviceId || "";
      if (
        previousDeviceId
        && openedDeviceId === previousDeviceId
        && navigator.mediaDevices?.enumerateDevices
      ) {
        const devices = (await navigator.mediaDevices.enumerateDevices())
          .filter((device) => device.kind === "videoinput");
        const matchingDevice = devices.find((device) => (
          device.deviceId !== previousDeviceId
          && inferredFacingMode(device.label, "") === requestedFacingMode
        ));
        const fallbackDevice = devices.find((device) => device.deviceId !== previousDeviceId);
        const alternative = matchingDevice || fallbackDevice;
        if (alternative) {
          const switched = await openCamera(requestedFacingMode, alternative.deviceId);
          if (!switched) await openCamera(previousFacingMode, previousDeviceId);
        }
      }
    });

    recordButton.addEventListener("click", () => {
      if (!stream) return;
      chunks = [];
      recorder = new MediaRecorder(stream, { mimeType: "video/webm" });
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener("stop", () => {
        const blob = new Blob(chunks, { type: "video/webm" });
        const file = new File([blob], `webcam-road-survey-${Date.now()}.webm`, { type: "video/webm" });
        const transfer = new DataTransfer();
        transfer.items.add(file);
        fileInput.files = transfer.files;
        setStatus(`Recorded ${Math.round(blob.size / 1024)} KB WEBM clip and attached it for upload.`);
      });
      recorder.start();
      recordButton.disabled = true;
      flipButton.disabled = true;
      stopButton.disabled = false;
      setStatus("Recording webcam clip...");
    });

    stopButton.addEventListener("click", () => {
      if (recorder && recorder.state !== "inactive") recorder.stop();
      releaseCamera();
      startButton.disabled = false;
      recordButton.disabled = true;
      stopButton.disabled = true;
    });

    window.addEventListener("beforeunload", releaseCamera, { once: true });
  }

  function initFleetCameraCapture() {
    const forms = document.querySelectorAll("[data-fleet-capture]");
    if (!forms.length) return;

    function recorderFormat() {
      const candidates = [
        { mimeType: "video/webm;codecs=vp9", extension: "webm" },
        { mimeType: "video/webm;codecs=vp8", extension: "webm" },
        { mimeType: "video/webm", extension: "webm" },
        { mimeType: "video/mp4", extension: "mp4" },
      ];
      return candidates.find((candidate) => (
        typeof MediaRecorder !== "undefined"
        && (!MediaRecorder.isTypeSupported || MediaRecorder.isTypeSupported(candidate.mimeType))
      )) || null;
    }

    forms.forEach((form) => {
      const preview = form.querySelector(".fleet-capture-preview");
      const fileInput = form.querySelector(".fleet-capture-file");
      const startButton = form.querySelector(".fleet-camera-start");
      const flipButton = form.querySelector(".fleet-camera-flip");
      const recordButton = form.querySelector(".fleet-camera-record");
      const stopButton = form.querySelector(".fleet-camera-stop");
      const submitButton = form.querySelector(".fleet-camera-submit");
      const cameraSelect = form.querySelector(".fleet-camera-source");
      const liveIndicator = form.querySelector(".fleet-live-indicator");
      const status = form.querySelector(".fleet-capture-status");
      if (!preview || !fileInput || !startButton || !flipButton || !recordButton || !stopButton || !submitButton || !cameraSelect) return;

      let stream = null;
      let recorder = null;
      let chunks = [];
      let capturedFile = null;
      let recordingTimer = 0;
      let currentFacingMode = "environment";
      const captureAllowed = !startButton.disabled;

      function setStatus(message) {
        if (status) status.textContent = message;
      }

      function selectedCameraName() {
        return cameraSelect.selectedOptions[0]?.textContent || "Camera";
      }

      async function refreshCameraList(preferredDeviceId = "") {
        if (!navigator.mediaDevices?.enumerateDevices) {
          cameraSelect.replaceChildren(new Option("Camera selection is not supported", ""));
          cameraSelect.disabled = true;
          return [];
        }

        const devices = (await navigator.mediaDevices.enumerateDevices())
          .filter((device) => device.kind === "videoinput");
        const activeDeviceId = stream?.getVideoTracks()[0]?.getSettings()?.deviceId || "";
        const requestedDeviceId = preferredDeviceId || activeDeviceId || cameraSelect.value;
        cameraSelect.replaceChildren();

        if (!devices.length) {
          cameraSelect.append(new Option("No cameras detected", ""));
          cameraSelect.disabled = true;
          return devices;
        }

        devices.forEach((device, index) => {
          cameraSelect.append(new Option(device.label || `Camera ${index + 1}`, device.deviceId));
        });
        if (requestedDeviceId && devices.some((device) => device.deviceId === requestedDeviceId)) {
          cameraSelect.value = requestedDeviceId;
        }
        cameraSelect.disabled = !captureAllowed || (recorder && recorder.state !== "inactive");
        return devices;
      }

      function releaseCamera() {
        window.clearTimeout(recordingTimer);
        recordingTimer = 0;
        stream?.getTracks().forEach((track) => track.stop());
        stream = null;
        preview.pause();
        preview.srcObject = null;
        preview.hidden = true;
        preview.classList.remove("is-user-facing");
        if (liveIndicator) liveIndicator.hidden = true;
        startButton.disabled = !captureAllowed;
        flipButton.disabled = true;
        recordButton.disabled = true;
        stopButton.disabled = true;
        stopButton.textContent = "End live";
        cameraSelect.disabled = !captureAllowed || cameraSelect.options.length === 0;
      }

      function stopRecording() {
        if (recorder && recorder.state !== "inactive") {
          recorder.stop();
          stopButton.disabled = true;
          setStatus("Finishing the captured clip...");
          return;
        }
        releaseCamera();
        setStatus("Live phone camera ended.");
      }

      async function openCamera(deviceId = "", facingMode = currentFacingMode) {
        if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
          setStatus("Live capture is not supported by this browser. Use a current mobile or desktop browser over HTTPS.");
          return false;
        }
        releaseCamera();
        startButton.disabled = true;
        flipButton.disabled = true;
        cameraSelect.disabled = true;
        setStatus(`Opening the ${facingMode === "user" ? "front" : "back"} camera...`);
        try {
          const videoConstraints = {
            width: { ideal: 1280 },
            height: { ideal: 720 },
          };
          if (deviceId) {
            videoConstraints.deviceId = { exact: deviceId };
          } else {
            videoConstraints.facingMode = { ideal: facingMode };
          }
          stream = await navigator.mediaDevices.getUserMedia({
            video: videoConstraints,
            audio: false,
          });
          preview.srcObject = stream;
          preview.hidden = false;
          await preview.play();
          if (liveIndicator) liveIndicator.hidden = false;
          const trackSettings = stream.getVideoTracks()[0]?.getSettings?.() || {};
          const openedDeviceId = trackSettings.deviceId || deviceId;
          await refreshCameraList(openedDeviceId).catch(() => {
            cameraSelect.replaceChildren(new Option("Current camera", openedDeviceId));
            cameraSelect.disabled = true;
          });
          currentFacingMode = trackSettings.facingMode || inferredFacingMode(selectedCameraName(), facingMode);
          updateFacingControl(preview, flipButton, currentFacingMode);
          startButton.disabled = true;
          flipButton.disabled = false;
          recordButton.disabled = false;
          stopButton.disabled = false;
          stopButton.textContent = "End live";
          const cameraName = cameraSelect.value ? selectedCameraName() : "Camera";
          setStatus(`${cameraName} is live. Point it toward the road, then record a clip up to 45 seconds.`);
          return true;
        } catch (error) {
          releaseCamera();
          await refreshCameraList().catch(() => {});
          setStatus("Camera access is unavailable. Check browser permission, camera availability, and use HTTPS or localhost.");
          return false;
        }
      }

      startButton.addEventListener("click", () => {
        openCamera(cameraSelect.value, currentFacingMode);
      });

      flipButton.addEventListener("click", async () => {
        if (!stream || (recorder && recorder.state !== "inactive")) return;
        const previousFacingMode = currentFacingMode;
        const previousDeviceId = stream.getVideoTracks()[0]?.getSettings?.().deviceId || "";
        const requestedFacingMode = currentFacingMode === "user" ? "environment" : "user";
        const opened = await openCamera("", requestedFacingMode);
        if (!opened) {
          await openCamera(previousDeviceId, previousFacingMode);
          setStatus("That camera could not be opened. The previous camera is active.");
          return;
        }

        const openedDeviceId = stream?.getVideoTracks()[0]?.getSettings?.().deviceId || "";
        if (
          previousDeviceId
          && openedDeviceId === previousDeviceId
          && navigator.mediaDevices?.enumerateDevices
        ) {
          const devices = (await navigator.mediaDevices.enumerateDevices())
            .filter((device) => device.kind === "videoinput");
          const matchingDevice = devices.find((device) => (
            device.deviceId !== previousDeviceId
            && inferredFacingMode(device.label, "") === requestedFacingMode
          ));
          const fallbackDevice = devices.find((device) => device.deviceId !== previousDeviceId);
          const alternative = matchingDevice || fallbackDevice;
          if (alternative) {
            const switched = await openCamera(alternative.deviceId, requestedFacingMode);
            if (!switched) await openCamera(previousDeviceId, previousFacingMode);
          }
        }
      });

      cameraSelect.addEventListener("change", () => {
        currentFacingMode = inferredFacingMode(selectedCameraName(), currentFacingMode);
        if (stream && (!recorder || recorder.state === "inactive")) {
          openCamera(cameraSelect.value, currentFacingMode);
        } else {
          setStatus(`${selectedCameraName()} selected. Start the live camera to open the preview.`);
        }
      });

      recordButton.addEventListener("click", () => {
        if (!stream) return;
        const format = recorderFormat();
        if (!format) {
          setStatus("This browser cannot record a supported WEBM or MP4 clip.");
          return;
        }
        chunks = [];
        capturedFile = null;
        submitButton.disabled = true;
        fileInput.value = "";
        try {
          recorder = new MediaRecorder(stream, { mimeType: format.mimeType });
        } catch (error) {
          setStatus("The camera opened, but video recording could not start on this browser.");
          return;
        }
        recorder.addEventListener("dataavailable", (event) => {
          if (event.data && event.data.size) chunks.push(event.data);
        });
        recorder.addEventListener("stop", () => {
          const blob = new Blob(chunks, { type: format.mimeType });
          if (!blob.size) {
            releaseCamera();
            setStatus("No video data was captured. Open the camera and try again.");
            return;
          }
          const safeDevice = (form.dataset.deviceName || form.dataset.deviceId || "fleet-camera")
            .replace(/[^a-z0-9]+/gi, "-")
            .replace(/^-+|-+$/g, "")
            .toLowerCase();
          capturedFile = new File(
            [blob],
            `${safeDevice || "fleet-camera"}-${Date.now()}.${format.extension}`,
            { type: format.mimeType }
          );
          try {
            const transfer = new DataTransfer();
            transfer.items.add(capturedFile);
            fileInput.files = transfer.files;
          } catch (error) {
            // The submit handler attaches the captured Blob on browsers that block programmatic file assignment.
          }
          releaseCamera();
          submitButton.disabled = false;
          setStatus(`Captured ${Math.max(1, Math.round(blob.size / 1024))} KB. Choose sensitivity, then send the clip.`);
        }, { once: true });
        recorder.start(1000);
        recordButton.disabled = true;
        flipButton.disabled = true;
        stopButton.disabled = false;
        stopButton.textContent = "Stop recording";
        cameraSelect.disabled = true;
        setStatus("Recording road clip... capture stops automatically after 45 seconds.");
        recordingTimer = window.setTimeout(stopRecording, 45000);
      });

      stopButton.addEventListener("click", stopRecording);

      form.addEventListener("submit", async (event) => {
        if (!capturedFile && !fileInput.files?.length) {
          event.preventDefault();
          setStatus("Record a clip before sending it to Video Analyzer.");
          return;
        }
        if (fileInput.files?.length) {
          submitButton.disabled = true;
          setStatus("Uploading captured clip to Video Analyzer...");
          return;
        }

        event.preventDefault();
        submitButton.disabled = true;
        setStatus("Uploading captured clip to Video Analyzer...");
        const data = new FormData(form);
        data.delete("video");
        data.append("video", capturedFile, capturedFile.name);
        try {
          const response = await fetch(form.action || window.location.href, {
            method: "POST",
            body: data,
            credentials: "same-origin",
          });
          if (!response.ok) throw new Error(`Upload failed with status ${response.status}`);
          window.location.assign(response.redirected ? response.url : window.location.href);
        } catch (error) {
          submitButton.disabled = false;
          setStatus("The clip could not be uploaded. Check the connection and try again.");
        }
      });

      window.addEventListener("beforeunload", releaseCamera, { once: true });
      refreshCameraList().catch(() => {
        cameraSelect.replaceChildren(new Option("Open camera to detect devices", ""));
        cameraSelect.disabled = true;
      });
      navigator.mediaDevices?.addEventListener?.("devicechange", () => {
        refreshCameraList().catch(() => {});
      });
    });
  }

  function initAnalyzerUploadWorkflow() {
    const uploadModal = document.getElementById("upload-analysis-modal");
    const uploadDialog = uploadModal?.querySelector(".analyzer-modal-dialog");
    const openUpload = document.getElementById("open-upload-modal");
    const closeUpload = document.getElementById("close-upload-modal");
    const uploadForm = document.getElementById("visualizer-upload-form");
    const uploadSubmit = document.getElementById("submit-video-analysis");
    const processingModal = document.getElementById("processing-modal");
    const processingDialog = processingModal?.querySelector(".processing-modal-dialog");
    const processingTitle = document.getElementById("processing-modal-title");
    const processingCopy = document.getElementById("processing-modal-copy");
    const processingProgress = processingModal?.querySelector(".processing-progress span");
    const dismissProcessing = document.getElementById("processing-modal-dismiss");
    const readyNotification = document.getElementById("analysis-ready-notification");
    const readyTitle = document.getElementById("analysis-ready-title");
    const readyCopy = document.getElementById("analysis-ready-copy");
    const viewResults = document.getElementById("view-analysis-results");
    const closeNotification = document.getElementById("close-analysis-notification");
    const stage = document.getElementById("visualizer-stage");
    const activeStatuses = ["queued", "retrying", "running"];
    let previousFocus = null;
    let statusPoll = 0;

    if (!uploadModal || !openUpload || !uploadForm || !processingModal) return;

    function syncModalOpenState() {
      const analyzerModalOpen = [uploadModal, processingModal].some((modal) => modal && !modal.hidden);
      const imageModalOpen = imageViewer && !imageViewer.hidden;
      document.body.classList.toggle("modal-open", Boolean(analyzerModalOpen || imageModalOpen));
    }

    function showUploadModal() {
      previousFocus = document.activeElement;
      uploadModal.hidden = false;
      syncModalOpenState();
      window.requestAnimationFrame(() => uploadDialog?.focus());
    }

    function hideUploadModal(restoreFocus = true) {
      uploadModal.hidden = true;
      syncModalOpenState();
      if (restoreFocus) (previousFocus || openUpload)?.focus?.();
    }

    function showProcessingModal(message) {
      uploadModal.hidden = true;
      processingModal.hidden = false;
      if (processingTitle) processingTitle.textContent = "Processing your video";
      if (processingCopy && message) processingCopy.textContent = message;
      if (processingProgress) processingProgress.style.removeProperty("width");
      if (dismissProcessing) dismissProcessing.hidden = false;
      syncModalOpenState();
      window.requestAnimationFrame(() => processingDialog?.focus());
    }

    function hideProcessingModal() {
      processingModal.hidden = true;
      syncModalOpenState();
    }

    function showReadyNotification(data) {
      hideProcessingModal();
      if (!readyNotification) return;
      const defectTotal = Number(data.total_unique_potholes || 0) + Number(data.road_damage_count || 0);
      if (readyTitle) readyTitle.textContent = "Video analysis is ready";
      if (readyCopy) {
        readyCopy.textContent = defectTotal
          ? `Processing is complete with ${defectTotal} tracked ${defectTotal === 1 ? "defect" : "defects"}.`
          : "Processing is complete. Open the analysis to review the result.";
      }
      if (viewResults) viewResults.hidden = false;
      readyNotification.classList.remove("is-error");
      readyNotification.hidden = false;
      readyNotification.focus?.();
    }

    function showProcessingFailure(data) {
      hideProcessingModal();
      if (!readyNotification) return;
      if (readyTitle) readyTitle.textContent = data.status === "cancelled" ? "Video analysis was cancelled" : "Video analysis needs attention";
      if (readyCopy) readyCopy.textContent = data.error_message || "The video could not be processed. Retry the analysis when ready.";
      if (viewResults) viewResults.hidden = true;
      readyNotification.classList.add("is-error");
      readyNotification.hidden = false;
    }

    function updateProcessingState(data) {
      if (!processingCopy) return;
      const current = Number(data.current_frame || data.frames_processed || 0);
      const total = Number(data.frame_count || 0);
      if (data.status === "queued" || data.status === "retrying") {
        processingCopy.textContent = data.status === "retrying"
          ? "The analyzer is retrying this video. Processing will resume automatically."
          : "Your video is queued and will begin processing shortly.";
      } else if (total > 0) {
        const percent = Math.min(100, Math.max(2, (current / total) * 100));
        processingCopy.textContent = `Detecting road defects — frame ${current.toLocaleString()} of ${total.toLocaleString()}.`;
        if (processingProgress) processingProgress.style.width = `${percent}%`;
      } else {
        processingCopy.textContent = `Detecting and tracking road defects${current ? ` — ${current.toLocaleString()} frames processed` : ""}.`;
      }
    }

    async function pollAnalysisStatus() {
      const statusUrl = stage?.dataset.statusUrl;
      if (!statusUrl || !activeStatuses.includes(stage.dataset.analysisStatus || "")) return;
      try {
        const response = await fetch(statusUrl, {
          headers: { "X-Requested-With": "XMLHttpRequest" },
          cache: "no-store",
        });
        if (!response.ok) return;
        const data = await response.json();
        stage.dataset.analysisStatus = data.status;
        if (activeStatuses.includes(data.status)) {
          updateProcessingState(data);
          return;
        }
        window.clearInterval(statusPoll);
        if (data.status === "complete") {
          showReadyNotification(data);
        } else {
          showProcessingFailure(data);
        }
      } catch (error) {
        if (processingCopy && !processingModal.hidden) {
          processingCopy.textContent = "Processing continues in the background. Reconnecting to live status…";
        }
      }
    }

    openUpload.addEventListener("click", showUploadModal);
    closeUpload?.addEventListener("click", () => hideUploadModal());
    uploadModal.querySelectorAll("[data-close-upload-modal]").forEach((control) => {
      control.addEventListener("click", () => hideUploadModal());
    });
    dismissProcessing?.addEventListener("click", hideProcessingModal);
    closeNotification?.addEventListener("click", () => {
      if (readyNotification) readyNotification.hidden = true;
    });
    uploadForm.addEventListener("submit", () => {
      if (!uploadForm.checkValidity()) return;
      if (uploadSubmit) {
        uploadSubmit.disabled = true;
        uploadSubmit.textContent = "Uploading…";
      }
      showProcessingModal("Uploading the footage and preparing pothole detection…");
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!uploadModal.hidden) {
        hideUploadModal();
      } else if (!processingModal.hidden) {
        hideProcessingModal();
      }
    });

    if (
      stage
      && stage.dataset.continuous !== "true"
      && activeStatuses.includes(stage.dataset.analysisStatus || "")
    ) {
      const initialStatus = stage.dataset.analysisStatus;
      showProcessingModal(
        initialStatus === "running"
          ? "Detecting and tracking road defects…"
          : "Your video is queued and will begin processing shortly."
      );
      pollAnalysisStatus();
      statusPoll = window.setInterval(pollAnalysisStatus, 2000);
      window.addEventListener("beforeunload", () => window.clearInterval(statusPoll), { once: true });
    }
  }

  function initContinuousVideoVisualizer(stage) {
    const statusUrl = stage.dataset.statusUrl;
    const preview = document.getElementById("continuous-live-preview");
    const waiting = document.getElementById("continuous-live-wait");
    const liveStatus = document.getElementById("continuous-live-status");
    const statusBadge = document.querySelector(".analysis-summary-panel .status-badge");
    const hudTime = document.getElementById("hud-time");
    let statusPoll = 0;

    const setMetric = (name, value) => {
      document.querySelectorAll(`[data-live-metric="${name}"]`).forEach((node) => {
        node.textContent = String(value);
      });
    };

    const decimal = (value, places = 2) => {
      const number = Number(value || 0);
      return Number.isFinite(number) ? number.toFixed(places) : "0.00";
    };

    const formatDuration = (value) => {
      const seconds = Math.max(0, Math.floor(Number(value) || 0));
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      const remainder = seconds % 60;
      return hours
        ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
        : `${minutes}:${String(remainder).padStart(2, "0")}`;
    };

    async function pollLiveStatus() {
      if (!statusUrl) return;
      try {
        const response = await fetch(statusUrl, {
          headers: { "X-Requested-With": "XMLHttpRequest" },
          cache: "no-store",
        });
        if (!response.ok) return;
        const data = await response.json();
        stage.dataset.analysisStatus = data.status;
        setMetric("unique", data.total_unique_potholes);
        setMetric("road-damage", data.road_damage_count);
        setMetric("total", `${Number(data.total_unique_potholes || 0) + Number(data.road_damage_count || 0)} total`);
        setMetric("detections", data.total_detections);
        setMetric("confidence", data.average_confidence == null ? "--" : decimal(data.average_confidence, 4));
        setMetric("frames", data.frames_processed);
        setMetric("current-frame", data.current_frame);
        setMetric("processing-fps", decimal(data.average_processing_fps, 2));
        setMetric("source-fps", decimal(data.source_processing_fps, 2));
        setMetric("realtime-factor", decimal(data.realtime_factor, 3));
        if (hudTime) hudTime.textContent = formatDuration(data.duration_seconds);
        if (statusBadge) {
          statusBadge.textContent = data.status;
          statusBadge.className = `status-badge ${data.status}`;
        }
        if (data.live_preview_url && preview) {
          const separator = data.live_preview_url.includes("?") ? "&" : "?";
          preview.src = `${data.live_preview_url}${separator}frame=${data.current_frame}`;
          preview.hidden = false;
          if (waiting) waiting.hidden = true;
        }
        if (liveStatus) {
          if (data.stop_requested) {
            liveStatus.textContent = "Stopping safely and finalizing tracks and snapshots...";
          } else if (data.error_message) {
            liveStatus.textContent = data.error_message;
          } else if (data.status === "queued" || data.status === "retrying") {
            liveStatus.textContent = "Waiting for the continuous analysis worker...";
          } else {
            liveStatus.textContent = "Continuous detection is active until an operator stops it.";
          }
        }
        if (!["queued", "retrying", "running"].includes(data.status)) {
          window.clearInterval(statusPoll);
          window.location.reload();
        }
      } catch (error) {
        if (liveStatus) liveStatus.textContent = "Live status is temporarily unavailable; retrying automatically.";
      }
    }

    if (["queued", "retrying", "running"].includes(stage.dataset.analysisStatus || "")) {
      pollLiveStatus();
      statusPoll = window.setInterval(pollLiveStatus, 1000);
      window.addEventListener("beforeunload", () => window.clearInterval(statusPoll), { once: true });
    }
  }

  function initVideoVisualizer() {
    const video = document.getElementById("visualizer-video");
    const payload = document.getElementById("video-frame-detections");
    const overlay = document.getElementById("video-overlay-layer");
    const stage = document.getElementById("visualizer-stage");
    const mediaStatus = document.getElementById("video-media-status");
    if (!stage) return;
    if (stage.dataset.continuous === "true") {
      initContinuousVideoVisualizer(stage);
      return;
    }
    if (!video || !payload || !overlay) {
      if (["queued", "retrying", "running"].includes(stage.dataset.analysisStatus || "")) {
        window.setTimeout(() => window.location.reload(), 3000);
      }
      return;
    }

    let frameDetections = [];
    try {
      frameDetections = JSON.parse(payload.textContent || "[]");
    } catch (error) {
      frameDetections = [];
    }
    const playButton = document.getElementById("viz-play");
    const stopButton = document.getElementById("viz-stop");
    const prevFrame = document.getElementById("viz-prev-frame");
    const nextFrame = document.getElementById("viz-next-frame");
    const speed = document.getElementById("viz-speed");
    const confFilter = document.getElementById("viz-conf-filter");
    const iouFilter = document.getElementById("viz-iou-filter");
    const labelsToggle = document.getElementById("viz-toggle-labels");
    const scoresToggle = document.getElementById("viz-toggle-confidence");
    const trackIdsToggle = document.getElementById("viz-toggle-trackids");
    const masksToggle = document.getElementById("viz-toggle-masks");
    const boxesToggle = document.getElementById("viz-toggle-boxes");
    const gpsToggle = document.getElementById("viz-toggle-gps");
    const fullscreen = document.getElementById("viz-fullscreen");
    const timeline = document.getElementById("video-timeline");
    const timelineScrubber = document.getElementById("video-timeline-scrubber");
    const timelineProgress = document.getElementById("timeline-progress");
    const timelineReadout = document.getElementById("timeline-readout");
    const timelineMarkers = Array.from(timeline?.querySelectorAll("[data-marker-time]") || []);
    const hudFrame = document.getElementById("hud-frame");
    const hudTime = document.getElementById("hud-time");
    const hudThreshold = document.getElementById("hud-threshold");
    const nominalFps = Math.max(1, Number(document.querySelector("[data-video-fps]")?.dataset.videoFps || 30));
    const requestedStart = Number(new URLSearchParams(window.location.search).get("t") || 0);
    let overlayAnimationFrame = 0;
    function visibleVideoRect() {
      const overlayRect = overlay.getBoundingClientRect();
      const elementRect = video.getBoundingClientRect();
      const videoRatio = video.videoWidth && video.videoHeight ? video.videoWidth / video.videoHeight : 16 / 9;
      const elementRatio = elementRect.width / Math.max(elementRect.height, 1);
      let width = elementRect.width;
      let height = elementRect.height;
      let left = elementRect.left - overlayRect.left;
      let top = elementRect.top - overlayRect.top;
      if (elementRatio > videoRatio) {
        width = height * videoRatio;
        left += (elementRect.width - width) / 2;
      } else {
        height = width / videoRatio;
        top += (elementRect.height - height) / 2;
      }
      return { left, top, width, height };
    }

    function nearestFrameDetections() {
      if (!frameDetections.length) return [];
      const current = video.currentTime || 0;
      let best = frameDetections[0];
      let distance = Math.abs(Number(best.timestamp || 0) - current);
      for (const item of frameDetections) {
        const nextDistance = Math.abs(Number(item.timestamp || 0) - current);
        if (nextDistance < distance) {
          best = item;
          distance = nextDistance;
        }
      }
      return distance <= 0.45 ? (best.detections || []) : [];
    }

    function timelineDuration() {
      const mediaDuration = Number(video.duration);
      if (Number.isFinite(mediaDuration) && mediaDuration > 0) return mediaDuration;
      const storedDuration = Number(timeline?.dataset.duration || 0);
      return Number.isFinite(storedDuration) && storedDuration > 0 ? storedDuration : 0;
    }

    function formatTimelineTime(value) {
      const seconds = Math.max(0, Number(value) || 0);
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      const remainder = Math.floor(seconds % 60);
      return hours
        ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
        : `${minutes}:${String(remainder).padStart(2, "0")}`;
    }

    function markerName(marker) {
      const label = marker.dataset.markerLabel === "road_damage" ? "Road damage" : "Pothole";
      return `${label} P${marker.dataset.markerTrack}`;
    }

    function arrangeTimelineMarkers() {
      const duration = timelineDuration();
      const proximity = Math.max(0.4, duration * 0.012);
      let clusterStart = Number.NEGATIVE_INFINITY;
      let clusterIndex = 0;
      timelineMarkers.forEach((marker, index) => {
        const time = Number(marker.dataset.markerTime || 0);
        if (time - clusterStart > proximity) {
          clusterStart = time;
          clusterIndex = 0;
        } else {
          clusterIndex += 1;
        }
        const lane = [-1, 0, 1][clusterIndex % 3];
        marker.style.setProperty("--marker-lane", String(lane));
        marker.style.zIndex = String(10 + index + clusterIndex);
      });
    }

    function syncTimeline() {
      if (!timeline) return;
      const duration = timelineDuration();
      const current = Math.max(0, Math.min(Number(video.currentTime) || 0, duration || Number(video.currentTime) || 0));
      if (timelineScrubber) {
        timelineScrubber.max = String(duration || 1);
        timelineScrubber.value = String(current);
        timelineScrubber.disabled = duration <= 0;
        timelineScrubber.setAttribute("aria-valuetext", `${formatTimelineTime(current)} of ${duration ? formatTimelineTime(duration) : "unknown duration"}`);
      }
      if (timelineProgress) {
        timelineProgress.style.width = `${duration ? Math.max(0, Math.min(100, (current / duration) * 100)) : 0}%`;
      }
      let closest = null;
      let closestDistance = Number.POSITIVE_INFINITY;
      timelineMarkers.forEach((marker) => {
        const distance = Math.abs(Number(marker.dataset.markerTime || 0) - current);
        if (distance < closestDistance) {
          closest = marker;
          closestDistance = distance;
        }
        marker.classList.remove("is-active");
        marker.removeAttribute("aria-current");
      });
      const activeWindow = Math.max(0.5, Math.min(2, duration * 0.004));
      const active = closest && closestDistance <= activeWindow ? closest : null;
      if (active) {
        active.classList.add("is-active");
        active.setAttribute("aria-current", "true");
      }
      if (timelineReadout) {
        const timeText = `${formatTimelineTime(current)} / ${duration ? formatTimelineTime(duration) : "--:--"}`;
        timelineReadout.textContent = active ? `${timeText} · ${markerName(active)}` : timeText;
      }
    }

    function seekToMarker(marker, focus = false) {
      video.currentTime = Math.max(0, Number(marker.dataset.markerTime || 0));
      if (focus) marker.focus();
      renderOverlay();
    }

    function renderOverlay() {
      overlay.innerHTML = "";
      const threshold = Number(confFilter?.value || 1) / 100;
      const rect = visibleVideoRect();
      const currentFrame = Math.max(0, Math.round((video.currentTime || 0) * nominalFps));
      if (hudFrame) hudFrame.textContent = `Frame ${currentFrame}`;
      if (hudTime) hudTime.textContent = `${(video.currentTime || 0).toFixed(2)}s`;
      if (hudThreshold) hudThreshold.textContent = `Conf ${confFilter?.value || 0}%`;

      nearestFrameDetections()
        .filter((detection) => Number(detection.confidence || 0) >= threshold)
        .forEach((detection) => {
          const box = detection.bbox || detection;
          const polygon = Array.isArray(detection.segmentation_points) ? detection.segmentation_points : [];
          const hasMask = polygon.length >= 3;

          if ((!masksToggle || masksToggle.checked) && hasMask) {
            const maskColor = detection.mask_source === "estimated" ? "#f59e0b" : "#d946ef";
            const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
            svg.classList.add("video-overlay-mask");
            svg.setAttribute("viewBox", `0 0 ${rect.width} ${rect.height}`);
            svg.style.left = `${rect.left}px`;
            svg.style.top = `${rect.top}px`;
            svg.style.width = `${rect.width}px`;
            svg.style.height = `${rect.height}px`;
            const patternId = `mask-hatch-${detection.track_id}-${currentFrame}`;
            const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
            const pattern = document.createElementNS("http://www.w3.org/2000/svg", "pattern");
            pattern.setAttribute("id", patternId);
            pattern.setAttribute("width", "10");
            pattern.setAttribute("height", "10");
            pattern.setAttribute("patternUnits", "userSpaceOnUse");
            const hatch = document.createElementNS("http://www.w3.org/2000/svg", "path");
            hatch.setAttribute("d", "M-2 2 L2 -2 M0 10 L10 0 M8 12 L12 8");
            hatch.setAttribute("stroke", maskColor);
            hatch.setAttribute("stroke-width", "4");
            pattern.appendChild(hatch);
            defs.appendChild(pattern);
            const shape = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
            shape.setAttribute(
              "points",
              polygon
                .map((point) => `${Number(point[0]) * rect.width},${Number(point[1]) * rect.height}`)
                .join(" ")
            );
            shape.setAttribute("fill", `url(#${patternId})`);
            svg.append(defs, shape);
            overlay.appendChild(svg);
          }

          if (boxesToggle && !boxesToggle.checked) return;
          const item = document.createElement("div");
          item.className = "video-overlay-box";
          item.style.left = `${rect.left + (Number(box.center_x) - Number(box.width) / 2) * rect.width}px`;
          item.style.top = `${rect.top + (Number(box.center_y) - Number(box.height) / 2) * rect.height}px`;
          item.style.width = `${Number(box.width) * rect.width}px`;
          item.style.height = `${Number(box.height) * rect.height}px`;
          const parts = [];
          if (!labelsToggle || labelsToggle.checked) {
            parts.push(detection.label === "road_damage" ? "Road damage" : "Pothole");
            if (detection.mask_source === "estimated") parts.push("estimated mask");
          }
          if (!trackIdsToggle || trackIdsToggle.checked) parts.push(`P${detection.track_id}`);
          if (!scoresToggle || scoresToggle.checked) parts.push(`${Math.round(Number(detection.confidence || 0) * 100)}%`);
          if (!gpsToggle || gpsToggle.checked) {
            if (detection.lat && detection.lng) parts.push(`${Number(detection.lat).toFixed(5)}, ${Number(detection.lng).toFixed(5)}`);
          }
          item.innerHTML = `<span>${parts.join(" ")}</span>`;
          overlay.appendChild(item);
        });
      syncTimeline();
    }

    function animateOverlay() {
      renderOverlay();
      if (!video.paused && !video.ended) {
        overlayAnimationFrame = window.requestAnimationFrame(animateOverlay);
      }
    }

    function stopOverlayAnimation() {
      if (overlayAnimationFrame) window.cancelAnimationFrame(overlayAnimationFrame);
      overlayAnimationFrame = 0;
    }

    playButton?.addEventListener("click", () => {
      if (video.paused) {
        video.play();
        playButton.textContent = "Pause";
      } else {
        video.pause();
        playButton.textContent = "Play";
      }
    });
    stopButton?.addEventListener("click", () => {
      video.pause();
      video.currentTime = 0;
      if (playButton) playButton.textContent = "Play";
      renderOverlay();
    });
    prevFrame?.addEventListener("click", () => {
      video.pause();
      video.currentTime = Math.max(0, video.currentTime - 1 / nominalFps);
      renderOverlay();
    });
    nextFrame?.addEventListener("click", () => {
      video.pause();
      video.currentTime = Math.min(video.duration || video.currentTime, video.currentTime + 1 / nominalFps);
      renderOverlay();
    });
    speed?.addEventListener("change", () => {
      video.playbackRate = Number(speed.value || 1);
    });
    [confFilter, iouFilter, labelsToggle, scoresToggle, trackIdsToggle, masksToggle, boxesToggle, gpsToggle].forEach((control) => {
      control?.addEventListener("input", renderOverlay);
      control?.addEventListener("change", renderOverlay);
    });
    fullscreen?.addEventListener("click", async () => {
      if (!document.fullscreenElement) {
        await stage.requestFullscreen?.();
      } else {
        await document.exitFullscreen?.();
      }
    });
    timelineScrubber?.addEventListener("input", () => {
      video.currentTime = Number(timelineScrubber.value || 0);
      renderOverlay();
    });
    timelineMarkers.forEach((marker, index) => {
      marker.addEventListener("click", () => seekToMarker(marker));
      marker.addEventListener("keydown", (event) => {
        let targetIndex = null;
        if (event.key === "ArrowLeft" || event.key === "ArrowDown") targetIndex = Math.max(0, index - 1);
        if (event.key === "ArrowRight" || event.key === "ArrowUp") targetIndex = Math.min(timelineMarkers.length - 1, index + 1);
        if (event.key === "Home") targetIndex = 0;
        if (event.key === "End") targetIndex = timelineMarkers.length - 1;
        if (targetIndex === null) return;
        event.preventDefault();
        seekToMarker(timelineMarkers[targetIndex], true);
      });
    });
    video.addEventListener("timeupdate", renderOverlay);
    video.addEventListener("loadedmetadata", () => {
      if (mediaStatus) mediaStatus.hidden = true;
      if (requestedStart > 0 && Number.isFinite(requestedStart)) {
        video.currentTime = Math.min(requestedStart, video.duration || requestedStart);
      }
      arrangeTimelineMarkers();
      renderOverlay();
    });
    video.addEventListener("error", () => {
      if (mediaStatus) mediaStatus.hidden = false;
    });
    video.addEventListener("pause", () => {
      stopOverlayAnimation();
      renderOverlay();
      if (playButton) playButton.textContent = "Play";
    });
    video.addEventListener("play", () => {
      stopOverlayAnimation();
      overlayAnimationFrame = window.requestAnimationFrame(animateOverlay);
      if (playButton) playButton.textContent = "Pause";
    });
    video.addEventListener("ended", stopOverlayAnimation);
    window.addEventListener("resize", () => {
      arrangeTimelineMarkers();
      renderOverlay();
    });
    const videoResizeObserver = "ResizeObserver" in window
      ? new ResizeObserver(() => {
          arrangeTimelineMarkers();
          renderOverlay();
        })
      : null;
    videoResizeObserver?.observe(stage);
    videoResizeObserver?.observe(video);
    window.addEventListener("beforeunload", () => videoResizeObserver?.disconnect(), { once: true });
    arrangeTimelineMarkers();
    renderOverlay();
  }

  initAnalyzerUploadWorkflow();
  initWebcamRecorder();
  initFleetCameraCapture();
  initVideoVisualizer();

  document.querySelectorAll("[data-target-report]").forEach((pin) => {
    pin.addEventListener("click", () => {
      const target = document.getElementById(`report-${pin.dataset.targetReport}`);
      target?.scrollIntoView({ behavior: "smooth", block: "center" });
      target?.classList.add("is-active");
      window.setTimeout(() => target?.classList.remove("is-active"), 1400);
    });
  });

  if (!payload || !scene) return;

  const detections = JSON.parse(payload.textContent || "[]")
    .sort((a, b) => a.timecode_seconds - b.timecode_seconds)
    .map((event) => ({
      ...event,
      playback_seconds: Number(event.timecode_seconds),
    }));
  let duration = Number(scene.dataset.duration || 40);
  const detectCount = document.getElementById("detect-count");
  const fpsChip = document.getElementById("fps-chip");
  const playToggle = document.getElementById("play-toggle");
  const muteToggle = document.getElementById("mute-toggle");
  const fullscreenToggle = document.getElementById("fullscreen-toggle");
  const moreToggle = document.getElementById("more-toggle");
  const moreMenu = document.getElementById("more-menu");
  const restartAnalysis = document.getElementById("restart-analysis");
  const showAllDetections = document.getElementById("show-all-detections");
  const hideAllDetections = document.getElementById("hide-all-detections");
  const hideActionsMenu = document.getElementById("hide-actions-menu");
  const recordedMode = document.getElementById("recorded-mode");
  const liveMode = document.getElementById("live-mode");
  const liveBanner = document.getElementById("live-banner");
  const sourceTitle = document.getElementById("source-title");
  const sourceCopy = document.getElementById("source-copy");
  const sourceStatus = document.getElementById("source-status");
  const activeSource = document.getElementById("active-source");
  const timelineLabel = document.getElementById("timeline-label");
  const timelineProgress = document.getElementById("timeline-progress");
  const videoFrame = document.getElementById("video-frame");
  const uploadedVideo = document.getElementById("uploaded-video");
  const visibleEventCount = document.getElementById("visible-event-count");
  const metricDetections = document.getElementById("metric-detections");
  const boxes = Array.from(document.querySelectorAll(".box[data-event-code]"));
  const logRows = Array.from(document.querySelectorAll(".defect-list article[data-event-code]"));
  const captureWindowSeconds = 2.75;

  let currentTime = 0;
  let playing = false;
  let lastTick = 0;
  let rafId = 0;
  let liveModeEnabled = false;
  let latestActiveCode = "";
  let suppressOverlay = false;
  let forceShowAllOverlays = false;
  const streamSourceMessage = "Stream source selected. Configure a live camera feed before running pavement defect assessment.";

  function syncOverlayToVideoFrame() {
    if (!uploadedVideo || !scene || !videoFrame) return;
    const frameRect = videoFrame.getBoundingClientRect();
    const videoRect = uploadedVideo.getBoundingClientRect();
    if (!frameRect.width || !frameRect.height || !videoRect.width || !videoRect.height) return;
    scene.style.left = `${videoRect.left - frameRect.left}px`;
    scene.style.top = `${videoRect.top - frameRect.top}px`;
    scene.style.width = `${videoRect.width}px`;
    scene.style.height = `${videoRect.height}px`;
  }

  function formatTime(seconds) {
    const safe = Math.max(0, Math.floor(seconds));
    return `0:${String(safe).padStart(2, "0")}`;
  }

  function setProgress(seconds) {
    const percent = duration > 0 ? Math.min(100, (seconds / duration) * 100) : 0;
    timelineProgress.style.setProperty("--progress", `${percent}%`);
    timelineLabel.textContent = `${formatTime(seconds)} / ${duration}s`;
  }

  function visibleRows() {
    return logRows.filter((row) => !row.hidden);
  }

  function render(seconds) {
    const active = detections.filter((event) => event.playback_seconds <= seconds);
    const activeCodes = new Set(active.map((event) => event.event_code));
    const visibleCaptureCodes = new Set(
      detections
        .filter((event) => seconds >= event.playback_seconds && seconds <= event.playback_seconds + captureWindowSeconds)
        .map((event) => event.event_code)
    );
    const latest = active[active.length - 1];

    boxes.forEach((box) => {
      const row = logRows.find((item) => item.dataset.eventCode === box.dataset.eventCode);
      const filteredOut = row?.hidden;
      const visibleCodeSet = forceShowAllOverlays ? activeCodes : visibleCaptureCodes;
      const visible = !suppressOverlay && visibleCodeSet.has(box.dataset.eventCode) && !filteredOut;
      box.classList.toggle("is-hidden", !visible);
      box.classList.toggle("is-live", visible);
      box.classList.toggle("is-persistent", forceShowAllOverlays && visible);
      box.classList.toggle("is-fading", visible);
    });

    logRows.forEach((row) => {
      const reached = activeCodes.has(row.dataset.eventCode);
      row.classList.toggle("is-detected", reached);
      row.classList.toggle("is-pending", !reached);
      row.classList.toggle("is-active", latest?.event_code === row.dataset.eventCode);
    });

    if (latest && latest.event_code !== latestActiveCode) {
      latestActiveCode = latest.event_code;
      const row = logRows.find((item) => item.dataset.eventCode === latest.event_code);
      row?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    const activeVisibleCount = active.filter((event) => {
      const row = logRows.find((item) => item.dataset.eventCode === event.event_code);
      return !row?.hidden;
    }).length;

    detectCount.textContent = `DEFECT - ${activeVisibleCount}`;
    if (visibleEventCount) visibleEventCount.textContent = String(visibleRows().length);
    if (metricDetections) metricDetections.textContent = String(detections.length);
    if (fpsChip) {
      const base = liveModeEnabled ? 54 : 57;
      const fps = base + Math.round((Math.sin(seconds * 1.7) + 1) * 2);
      fpsChip.textContent = `${fps} FPS`;
    }
    setProgress(seconds);
  }

  function stopPlayback(label) {
    playing = false;
    cancelAnimationFrame(rafId);
    if (label) playToggle.textContent = label;
  }

  function tick(timestamp) {
    if (!playing) return;
    if (!lastTick) lastTick = timestamp;
    const delta = (timestamp - lastTick) / 1000;
    lastTick = timestamp;
    if (uploadedVideo && !liveModeEnabled) {
      currentTime = uploadedVideo.currentTime || currentTime;
    } else {
      currentTime += delta * (liveModeEnabled ? 1.65 : 1.25);
    }

    if (currentTime >= duration) {
      currentTime = duration;
      stopPlayback("Replay");
      render(currentTime);
      return;
    }

    render(currentTime);
    rafId = requestAnimationFrame(tick);
  }

  function togglePlayback() {
    if (playing) {
      stopPlayback("Play");
      uploadedVideo?.pause();
      return;
    }

    if (currentTime >= duration) currentTime = 0;
    if (uploadedVideo && !liveModeEnabled) uploadedVideo.currentTime = currentTime;
    suppressOverlay = false;
    forceShowAllOverlays = false;
    playing = true;
    lastTick = 0;
    playToggle.textContent = "Pause";
    uploadedVideo?.play?.().catch(() => {});
    rafId = requestAnimationFrame(tick);
  }

  function setMode(mode) {
    if (mode === "live" && liveMode?.disabled) return;
    liveModeEnabled = mode === "live";
    recordedMode?.classList.toggle("active", !liveModeEnabled);
    liveMode?.classList.toggle("active", liveModeEnabled);
    if (liveBanner) liveBanner.hidden = !liveModeEnabled;
    if (datasetSelect) datasetSelect.disabled = liveModeEnabled;
    if (uploadInput) uploadInput.disabled = liveModeEnabled;
    if (sourceTitle) sourceTitle.textContent = liveModeEnabled ? "Live dash-cam stream" : "Recorded footage";
    if (sourceCopy) {
      sourceCopy.textContent = liveModeEnabled
        ? "Live camera analysis requires a configured stream source."
        : "Upload MP4/WebM survey footage with route limits, then run pavement condition assessment.";
    }
    if (sourceStatus) {
      sourceStatus.textContent = liveModeEnabled
        ? streamSourceMessage
        : sourceStatus.dataset.recordedSource || "No upload selected";
    }
    if (activeSource) {
      activeSource.textContent = liveModeEnabled
        ? streamSourceMessage
        : activeSource.dataset.recordedSource || "No run yet";
    }
    currentTime = 0;
    if (uploadedVideo && !liveModeEnabled) uploadedVideo.currentTime = 0;
    stopPlayback("Play");
    render(0);
  }

  function applySearch(query) {
    const normalized = query.trim().toLowerCase();
    logRows.forEach((row) => {
      const text = (row.dataset.searchText || row.textContent || "").toLowerCase();
      row.hidden = normalized.length > 0 && !text.includes(normalized);
    });
    boxes.forEach((box) => {
      const row = logRows.find((item) => item.dataset.eventCode === box.dataset.eventCode);
      box.hidden = !!row?.hidden;
    });
    render(currentTime);
  }

  playToggle?.addEventListener("click", togglePlayback);
  uploadedVideo?.addEventListener("loadedmetadata", () => {
    duration = Number(uploadedVideo.duration || duration);
    scene.dataset.duration = String(duration);
    syncOverlayToVideoFrame();
    render(currentTime);
  });
  uploadedVideo?.addEventListener("loadeddata", () => {
    syncOverlayToVideoFrame();
    render(currentTime);
  });
  uploadedVideo?.addEventListener("timeupdate", () => {
    if (liveModeEnabled) return;
    currentTime = uploadedVideo.currentTime || 0;
    render(currentTime);
  });
  uploadedVideo?.addEventListener("ended", () => {
    currentTime = duration;
    stopPlayback("Replay");
    render(currentTime);
  });
  recordedMode?.addEventListener("click", () => setMode("recorded"));
  liveMode?.addEventListener("click", () => setMode("live"));

  muteToggle?.addEventListener("click", () => {
    if (uploadedVideo) uploadedVideo.muted = !uploadedVideo.muted;
    muteToggle.textContent = uploadedVideo && !uploadedVideo.muted ? "Mute" : "Unmute";
    scene.classList.toggle("is-muted");
  });

  fullscreenToggle?.addEventListener("click", async () => {
    if (!document.fullscreenElement) {
      await videoFrame?.requestFullscreen?.();
      fullscreenToggle.textContent = "Exit full";
    } else {
      await document.exitFullscreen?.();
      fullscreenToggle.textContent = "Full";
    }
  });

  document.addEventListener("fullscreenchange", () => {
    fullscreenToggle.textContent = document.fullscreenElement ? "Exit full" : "Full";
    window.setTimeout(syncOverlayToVideoFrame, 80);
  });
  window.addEventListener("resize", syncOverlayToVideoFrame);

  moreToggle?.addEventListener("click", () => {
    if (!moreMenu) return;
    moreMenu.hidden = !moreMenu.hidden;
    moreToggle.classList.toggle("active", !moreMenu.hidden);
  });
  hideActionsMenu?.addEventListener("click", () => {
    if (moreMenu) moreMenu.hidden = true;
    moreToggle?.classList.remove("active");
  });

  restartAnalysis?.addEventListener("click", () => {
    currentTime = 0;
    if (uploadedVideo && !liveModeEnabled) {
      uploadedVideo.pause();
      uploadedVideo.currentTime = 0;
    }
    latestActiveCode = "";
    suppressOverlay = false;
    forceShowAllOverlays = false;
    stopPlayback("Play");
    if (moreMenu) moreMenu.hidden = true;
    moreToggle?.classList.remove("active");
    render(0);
  });

  showAllDetections?.addEventListener("click", () => {
    currentTime = duration;
    if (uploadedVideo && !liveModeEnabled) {
      uploadedVideo.pause();
      uploadedVideo.currentTime = Math.max(0, Math.min(duration, uploadedVideo.duration || duration));
    }
    stopPlayback("Replay");
    suppressOverlay = false;
    forceShowAllOverlays = true;
    logRows.forEach((row) => {
      row.hidden = false;
      row.classList.add("is-detected");
      row.classList.remove("is-pending", "is-active");
    });
    if (moreMenu) moreMenu.hidden = true;
    moreToggle?.classList.remove("active");
    render(duration);
  });

  hideAllDetections?.addEventListener("click", () => {
    currentTime = 0;
    if (uploadedVideo && !liveModeEnabled) {
      uploadedVideo.pause();
      uploadedVideo.currentTime = 0;
    }
    latestActiveCode = "";
    suppressOverlay = true;
    forceShowAllOverlays = false;
    stopPlayback("Play");
    if (moreMenu) moreMenu.hidden = true;
    moreToggle?.classList.remove("active");
    render(0);
  });

  searchInput?.addEventListener("input", () => applySearch(searchInput.value));

  syncOverlayToVideoFrame();
  render(currentTime);
})();
