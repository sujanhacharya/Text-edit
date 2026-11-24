/* ==========================================================================
   InkPatch frontend
   Vanilla ES2017. No build step, no runtime dependencies, no CDN calls.
   ========================================================================== */

(function () {
  "use strict";

  /* --------------------------------------------------------------- helpers */

  var $ = function (selector, root) {
    return (root || document).querySelector(selector);
  };

  var $$ = function (selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  };

  var iconMarkup = function (name) {
    return '<svg class="icon" aria-hidden="true"><use href="#i-' + name + '"/></svg>';
  };

  var clamp = function (value, min, max) {
    return Math.min(max, Math.max(min, value));
  };

  var normalizeText = function (value) {
    return String(value == null ? "" : value).replace(/\r\n?/g, "\n");
  };

  var legacyCopyText = function (value) {
    var area = document.createElement("textarea");
    var active = document.activeElement;
    area.value = value;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "0";
    area.style.left = "-9999px";
    area.style.width = "1px";
    area.style.height = "1px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    var copied = false;
    try {
      area.focus();
      area.select();
      area.setSelectionRange(0, area.value.length);
      copied = document.execCommand("copy");
    } catch (error) {
      copied = false;
    }
    document.body.removeChild(area);
    if (active && typeof active.focus === "function") {
      try {
        active.focus({ preventScroll: true });
      } catch (error) {
        active.focus();
      }
    }
    return copied;
  };

  var copyToClipboard = function (value) {
    var text = normalizeText(value);
    if (!text) return Promise.resolve(false);
    var clipboard = window.navigator && window.navigator.clipboard;
    if (clipboard && typeof clipboard.writeText === "function") {
      try {
        return clipboard.writeText(text).then(function () {
          return true;
        }).catch(function () {
          return legacyCopyText(text);
        });
      } catch (error) {
        return Promise.resolve(legacyCopyText(text));
      }
    }
    return Promise.resolve(legacyCopyText(text));
  };

  var normalizeHex = function (value) {
    var match = /^#?([0-9a-f]{6})$/i.exec(String(value || ""));
    return match ? "#" + match[1].toLowerCase() : null;
  };

  var shadeHex = function (value, tone) {
    var hex = normalizeHex(value);
    if (!hex) return null;
    var level = Math.max(0, Math.min(100, Number(tone)));
    if (!isFinite(level)) level = 50;
    var weight = Math.abs(level - 50) / 50;
    var target = level < 50 ? 0 : 255;
    var channels = [1, 3, 5].map(function (start) {
      var channel = parseInt(hex.slice(start, start + 2), 16);
      return Math.round(channel + (target - channel) * weight);
    });
    return "#" + channels.map(function (channel) {
      return ("0" + channel.toString(16)).slice(-2);
    }).join("");
  };

  var truncate = function (value, limit) {
    var text = String(value == null ? "" : value);
    return text.length > limit ? text.slice(0, limit - 1).trim() + "…" : text;
  };

  var plural = function (count, singular, pluralForm) {
    return count + " " + (count === 1 ? singular : pluralForm || singular + "s");
  };

  var formatBytes = function (bytes) {
    if (!bytes) return "0 B";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  };

  var formatClock = function (date) {
    try {
      return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (error) {
      return "";
    }
  };

  var formatTtl = function (hours) {
    if (!hours) return "some time";
    if (hours < 24) return plural(hours, "hour");
    var days = Math.round(hours / 24);
    return plural(days, "day");
  };

  var familyName = function (value) {
    if (value === "serif") return "Serif";
    if (value === "mono") return "Mono";
    return "Sans";
  };

  var styleOf = function (line) {
    return line && line.style && typeof line.style === "object" ? line.style : {};
  };

  var faceSummary = function (style) {
    var found = styleOf({ style: style });
    var family = found.font_name || (found.font ? familyName(found.font) : "Sans");
    var weight = found.bold && found.italic
      ? " bold italic"
      : found.bold
        ? " bold"
        : found.italic
          ? " italic"
          : "";
    return family + weight;
  };

  var confidenceLevel = function (value) {
    var confidence = Number(value);
    if (!isFinite(confidence)) return "";
    if (confidence >= 80) return "";
    if (confidence >= 60) return "mid";
    return "low";
  };

  var prefersReducedMotion = function () {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  };

  var loadPreference = function (key, fallback) {
    try {
      var value = window.localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch (error) {
      return fallback;
    }
  };

  var savePreference = function (key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (error) {
      /* Private mode or disabled storage: preferences just won't persist. */
    }
  };

  var normalizeTheme = function (value) {
    return value === "dark" ? "dark" : "light";
  };

  var extractError = function (data, fallback) {
    if (!data) return fallback || "";
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail.map(function (item) {
        return item && item.msg ? item.msg : String(item);
      }).join("; ");
    }
    if (typeof data.message === "string") return data.message;
    return fallback || "";
  };

  /* ------------------------------------------------------------------ dom */

  var dom = {};

  var cacheDom = function () {
    [
      "landing-view", "editor-view", "brand-home", "engine-pill", "engine-pill-text",
      "theme-toggle", "new-image-btn", "download-btn", "engine-warning", "engine-warning-title",
      "engine-warning-text", "retry-engine", "upload-form", "dropzone", "file-input",
      "file-preview", "file-thumb", "file-name", "file-detail", "clear-file", "drop-limit",
      "lang-select", "psm-select", "upload-btn", "demo-btn", "back-btn", "doc-name",
      "doc-size", "doc-line-count", "doc-language", "doc-settings-btn", "mobile-lines-btn",
      "render-status", "render-status-text", "lines-panel", "close-lines", "lines-badge",
      "line-search", "search-clear", "filter-all-count", "filter-edited-count", "copy-all-btn",
      "dot-all-btn", "line-list",
      "line-empty", "line-empty-text", "stage", "canvas-scroll", "image-wrap", "doc-image",
      "overlay", "stage-loader", "stage-info", "stage-float-hint", "stage-float-text",
      "zoom-out", "zoom-in", "zoom-fit", "zoom-value", "zoom-readout", "live-preview",
      "dirty-readout", "preview-readout", "inspector", "close-inspector", "edit-tab",
      "document-tab", "edit-empty", "edit-form", "edit-line-id", "edit-conf",
      "copy-line-btn", "restore-line", "edit-text", "edit-removed-hint", "edit-original",
      "original-swatch", "custom-swatch", "custom-color-row", "color-picker", "color-hex",
      "original-bg-swatch", "custom-bg-swatch", "custom-bg-row", "bg-color-picker",
      "bg-color-hex", "bg-tone-range", "bg-tone-output",
      "font-select", "weight-select", "font-hint", "underline-select", "strike-select",
      "shadow-select", "scale-range", "scale-output",
      "remove-line", "remove-line-label", "add-text-btn", "style-source-select",
      "copy-style-btn", "copy-style-hint", "preview-btn", "doc-file", "doc-dims", "doc-lines", "doc-engine",
      "doc-warning", "doc-warning-text", "doc-lang", "doc-psm", "rescan-btn",
      "start-over-btn", "ttl-value", "busy-overlay", "busy-eyebrow", "busy-title",
      "busy-text", "busy-progress", "busy-progress-fill", "modal-root", "modal-icon",
      "modal-title", "modal-text", "modal-cancel", "modal-ok", "side-backdrop", "toasts",
      "retry-notice", "retry-notice-title", "retry-notice-text", "retry-all-btn",
      "retry-notice-close"
    ].forEach(function (id) {
      var key = id.replace(/-([a-z])/g, function (_, letter) {
        return letter.toUpperCase();
      });
      dom[key] = document.getElementById(id);
    });

    dom.filterTabs = $$(".filter-tab");
    dom.inspectorTabs = $$(".inspector-tab");
    dom.colorModes = $$('input[name="color-mode"]');
    dom.backgroundModes = $$('input[name="background-mode"]');
    dom.alignModes = $$('input[name="align"]');
    dom.colorLabels = $$(".radio-pill");
    dom.colorModeLabels = $$('.radio-pill input[name="color-mode"]');
    dom.backgroundModeLabels = $$('.radio-pill input[name="background-mode"]');
  };

  /* ---------------------------------------------------------------- state */

  var state = {
    view: "landing",
    health: null,
    languages: [],
    fontCatalog: [],
    engineAvailable: false,
    busy: false,
    file: null,
    fileUrl: null,
    doc: null,
    lines: [],
    lineIndex: new Map(),
    edits: new Map(),
    listNodes: new Map(),
    selectedId: null,
    hoverId: null,
    addingText: false,
    filter: "all",
    search: "",
    zoomMode: "fit",
    scale: 1,
    needFit: true,
    theme: normalizeTheme(loadPreference("inkpatch.theme", "light")),
    livePreview: loadPreference("inkpatch.livePreview", "1") !== "0",
    previewBusy: false,
    previewQueued: false,
    renderPromise: null,
    renderFailed: false,
    renderedSignature: "",
    previewVersion: 0,
    previewAt: null,
    renderTimer: null,
    stageHintTimer: null,
    imageToken: 0,
    downloadedSignature: null,
    mobilePanel: null,
    modalResolve: null,
    modalLastFocus: null,
    downloading: false,
    resizeTimer: null,
    pendingRetry: null,
    retryBusy: false
  };

  /* ---------------------------------------------------------------- theme */

  function applyTheme(theme, persist) {
    var next = normalizeTheme(theme);
    var dark = next === "dark";
    state.theme = next;
    document.documentElement.setAttribute("data-theme", next);
    if (persist) savePreference("inkpatch.theme", next);
    if (dom.themeToggle) {
      dom.themeToggle.setAttribute("aria-pressed", dark ? "true" : "false");
      dom.themeToggle.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
      dom.themeToggle.title = dark ? "Switch to light mode" : "Switch to dark mode";
    }
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", dark ? "#0e1316" : "#eef0ed");
    var scheme = document.querySelector('meta[name="color-scheme"]');
    if (scheme) scheme.setAttribute("content", dark ? "dark" : "light");
  }

  function toggleTheme() {
    applyTheme(state.theme === "dark" ? "light" : "dark", true);
  }

  /* -------------------------------------------------------------- api/io */

  function ApiError(message, status, payload) {
    this.name = "ApiError";
    this.message = message;
    this.status = status || 0;
    this.payload = payload || null;
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  var apiJSON = function (url, options) {
    var opts = Object.assign({ credentials: "same-origin" }, options || {});
    var headers = Object.assign({ Accept: "application/json" }, opts.headers || {});
    if (opts.body && typeof opts.body === "string" && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    opts.headers = headers;

    return fetch(url, opts).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        if (text) {
          try {
            data = JSON.parse(text);
          } catch (error) {
            data = null;
          }
        }
        if (!response.ok) {
          throw new ApiError(
            extractError(data) || "Request failed (" + response.status + ").",
            response.status,
            data
          );
        }
        return data;
      });
    }).catch(function (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError("Cannot reach the InkPatch server. Is it running?", 0, null);
    });
  };

  var uploadFile = function (formData, onProgress) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/documents");
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.onprogress = function (event) {
        if (event.lengthComputable && typeof onProgress === "function") {
          onProgress(event.loaded, event.total);
        }
      };
      xhr.onerror = function () {
        reject(new ApiError("The upload was interrupted. Check the server and try again.", 0));
      };
      xhr.onabort = function () {
        reject(new ApiError("The upload was cancelled.", 0));
      };
      xhr.onload = function () {
        var data = null;
        try {
          data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
        } catch (error) {
          data = null;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data);
        } else {
          reject(new ApiError(
            extractError(data) || "Upload failed (" + xhr.status + ").",
            xhr.status,
            data
          ));
        }
      };
      xhr.send(formData);
    });
  };

  /* --------------------------------------------------------------- toasts */

  var showToast = function (message, type, timeout) {
    if (!message) return;
    type = type || "info";
    var toast = document.createElement("div");
    toast.className = "toast is-" + type;
    var iconName = type === "error" ? "alert" : type === "success" ? "check" : type === "warning" ? "info" : "info";
    toast.innerHTML = iconMarkup(iconName);

    var copy = document.createElement("p");
    copy.textContent = message;
    toast.appendChild(copy);

    var close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "Dismiss notification");
    close.innerHTML = iconMarkup("close");
    toast.appendChild(close);

    var remove = function () {
      if (!toast.parentNode) return;
      toast.style.opacity = "0";
      toast.style.transform = "translateY(6px)";
      window.setTimeout(function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 160);
    };

    close.addEventListener("click", remove);
    dom.toasts.appendChild(toast);
    while (dom.toasts.querySelectorAll(".toast").length > 4) {
      var firstToast = dom.toasts.querySelector(".toast");
      if (!firstToast) break;
      dom.toasts.removeChild(firstToast);
    }
    window.setTimeout(remove, timeout || (type === "error" ? 8000 : type === "warning" ? 7000 : 4200));
  };

  /* --------------------------------------------------------------- retry */

  function hasPendingRetry(kind) {
    return !!(state.pendingRetry && state.pendingRetry.kinds.indexOf(kind) !== -1);
  }

  function pendingRetryText(pending) {
    var kinds = pending.kinds;
    var lines = state.doc && kinds.indexOf("render") !== -1 ? dirtyEdits().length : 0;
    if (lines > 0) {
      return plural(lines, "changed line") +
        " waiting. Retry all sends the latest changes in one request.";
    }
    if (kinds.indexOf("render") !== -1) {
      return "No changes remain. Retry all restores the original preview.";
    }
    var names = [];
    if (kinds.indexOf("upload") !== -1) names.push("upload");
    if (kinds.indexOf("demo") !== -1) names.push("demo");
    if (kinds.indexOf("rescan") !== -1) names.push("re-read");
    if (kinds.indexOf("download") !== -1) names.push("download");
    if (!names.length) return "The last request did not finish. Retry all tries again.";
    if (names.length === 1) {
      return "The " + names[0] + " did not finish. Retry all tries again.";
    }
    return "The " + names.slice(0, -1).join(", ") + " and " +
      names[names.length - 1] + " did not finish. Retry all tries them together.";
  }

  function updateRetryNotice() {
    if (!dom.retryNotice) return;
    var pending = state.pendingRetry;
    var visible = !!(pending && pending.kinds.length && !pending.dismissed);
    dom.retryNotice.hidden = !visible;
    if (!visible) return;
    dom.retryNoticeTitle.textContent = pending.failures > 1
      ? "Still no response from the server"
      : "The server did not respond";
    dom.retryNoticeText.textContent = pendingRetryText(pending);
    dom.retryAllBtn.disabled = state.retryBusy;
    var label = dom.retryAllBtn.querySelector("span");
    if (label) label.textContent = state.retryBusy ? "Retrying..." : "Retry all";
  }

  function clearPendingRetry() {
    state.pendingRetry = null;
    updateRetryNotice();
  }

  function resolveRetry(kind) {
    if (!state.pendingRetry) return;
    state.pendingRetry.kinds = state.pendingRetry.kinds.filter(function (item) {
      return item !== kind;
    });
    if (!state.pendingRetry.kinds.length) state.pendingRetry = null;
    updateRetryNotice();
  }

  function queueRetry(kind) {
    if (!kind) return;
    var pending = state.pendingRetry || { kinds: [], failures: 0, dismissed: false };
    if (pending.kinds.indexOf(kind) === -1) pending.kinds.push(kind);
    pending.failures += 1;
    pending.dismissed = false;
    state.pendingRetry = pending;
    updateRetryNotice();
  }

  async function retryAllPending() {
    if (state.retryBusy || state.busy) return;
    var pending = state.pendingRetry;
    if (!pending || !pending.kinds.length) return;
    var kinds = pending.kinds.slice();
    state.retryBusy = true;
    // Keep the failure count so a failed retry says "Still no response"
    // instead of presenting itself as a first failure.
    state.pendingRetry = { kinds: [], failures: pending.failures, dismissed: false };
    updateRetryNotice();

    try {
      if (kinds.indexOf("upload") !== -1) {
        await startUpload();
        return;
      }
      if (kinds.indexOf("demo") !== -1) {
        await startDemo();
        return;
      }
      if (!state.doc) return;
      if (kinds.indexOf("rescan") !== -1) {
        var rescanned = await rescanDocumentWithOptions({ confirmed: true });
        if (rescanned) return;
      }
      var rendered = true;
      if (kinds.indexOf("render") !== -1) {
        rendered = await renderCurrent({ force: true });
      }
      if (!rendered) {
        // A pending download cannot start without a fresh render, so keep that
        // intent for the next retry instead of dropping it.
        if (kinds.indexOf("download") !== -1 && state.pendingRetry &&
            state.pendingRetry.kinds.indexOf("download") === -1) {
          state.pendingRetry.kinds.push("download");
          updateRetryNotice();
        }
        return;
      }
      if (kinds.indexOf("download") !== -1) {
        await downloadImage();
      }
    } finally {
      state.retryBusy = false;
      if (state.pendingRetry && !state.pendingRetry.kinds.length) {
        state.pendingRetry = null;
      }
      updateRetryNotice();
    }
  }

  /* ---------------------------------------------------------------- modal */

  var resolveModal = function (value) {
    if (!state.modalResolve) return;
    var resolve = state.modalResolve;
    state.modalResolve = null;
    dom.modalRoot.hidden = true;
    if (state.modalLastFocus && typeof state.modalLastFocus.focus === "function") {
      try { state.modalLastFocus.focus(); } catch (error) { /* ignore */ }
    }
    state.modalLastFocus = null;
    resolve(value);
  };

  var confirmAction = function (options) {
    options = options || {};
    if (state.modalResolve) resolveModal(false);
    state.modalLastFocus = document.activeElement;
    dom.modalTitle.textContent = options.title || "Are you sure?";
    dom.modalText.textContent = options.text || "";
    dom.modalOk.textContent = options.okLabel || "Continue";
    dom.modalCancel.textContent = options.cancelLabel || "Cancel";
    dom.modalOk.className = "btn " + (options.danger === false ? "btn-primary" : "btn-danger");
    var use = dom.modalIcon.querySelector("use");
    if (use) use.setAttribute("href", options.danger === false ? "#i-refresh" : "#i-alert");
    dom.modalRoot.hidden = false;
    window.setTimeout(function () { dom.modalCancel.focus(); }, 20);

    return new Promise(function (resolve) {
      state.modalResolve = resolve;
    });
  };

  /* ----------------------------------------------------------------- busy */

  var setBusyProgress = function (value) {
    dom.busyProgress.hidden = false;
    dom.busyProgressFill.style.width = clamp(value, 0, 100) + "%";
  };

  var showBusy = function (options) {
    options = options || {};
    dom.busyEyebrow.textContent = options.eyebrow || "Working";
    dom.busyTitle.textContent = options.title || "Reading your image";
    dom.busyText.textContent = options.text || "This will only take a moment.";
    if (options.progress) {
      setBusyProgress(0);
    } else {
      dom.busyProgress.hidden = true;
      dom.busyProgressFill.style.width = "0%";
    }
    dom.busyOverlay.hidden = false;
    document.body.setAttribute("aria-busy", "true");
  };

  var setBusyPhase = function (eyebrow, title, text, keepProgress) {
    dom.busyEyebrow.textContent = eyebrow;
    dom.busyTitle.textContent = title;
    dom.busyText.textContent = text;
    if (!keepProgress) dom.busyProgress.hidden = true;
  };

  var hideBusy = function () {
    dom.busyOverlay.hidden = true;
    dom.busyProgress.hidden = true;
    dom.busyProgressFill.style.width = "0%";
    document.body.removeAttribute("aria-busy");
  };

  /* --------------------------------------------------------------- health */

  function setEnginePill(kind, text, title) {
    dom.enginePill.className = "engine-pill" + (kind ? " is-" + kind : "");
    dom.enginePillText.textContent = text;
    dom.enginePill.title = title || text;
  }

  function setEngineWarning(visible, title, text, offline) {
    dom.engineWarning.hidden = !visible;
    dom.engineWarning.classList.toggle("is-offline", !!offline);
    if (title) dom.engineWarningTitle.textContent = title;
    if (text) dom.engineWarningText.textContent = text;
  }

  function applyHealthLimits(health) {
    var limits = (health && health.limits) || {};
    var maxMb = Number(limits.max_upload_mb) || 20;
    var maxSide = Number(limits.max_side) || 2400;
    var ttl = Number(limits.ttl_hours) || 96;
    dom.dropLimit.textContent = "Up to " + maxMb + " MB · max " + maxSide + " px";
    dom.ttlValue.textContent = formatTtl(ttl);
    return maxMb;
  }

  function setSelectValue(select, value) {
    if (!select || !value) return;
    var found = false;
    for (var i = 0; i < select.options.length; i += 1) {
      if (select.options[i].value === value) {
        found = true;
        break;
      }
    }
    if (!found) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    }
    select.value = value;
  }

  function languageLabel(code) {
    for (var i = 0; i < state.languages.length; i += 1) {
      if (state.languages[i].code === code) return state.languages[i].name;
    }
    return code ? String(code).toUpperCase() : "Unknown";
  }

  function buildLanguageOptions() {
    var list = state.languages.length ? state.languages : [{ code: "eng", name: "English" }];
    var selects = [dom.langSelect, dom.docLang];
    selects.forEach(function (select) {
      if (!select) return;
      var previous = select.value;
      select.textContent = "";
      list.forEach(function (item) {
        var option = document.createElement("option");
        option.value = item.code;
        option.textContent = item.name || item.code;
        select.appendChild(option);
      });
      if (previous) {
        setSelectValue(select, previous);
      } else {
        var hasEnglish = list.some(function (item) { return item.code === "eng"; });
        select.value = hasEnglish ? "eng" : list[0].code;
      }
    });
  }

  function updateUploadEnabled() {
    var engineReady = state.engineAvailable;
    var demoReady = state.health ? state.health.demo_available !== false : false;
    dom.uploadBtn.disabled = !state.file || !engineReady || state.busy;
    dom.demoBtn.disabled = !engineReady || !demoReady || state.busy;
    dom.langSelect.disabled = state.busy;
    dom.psmSelect.disabled = state.busy;
    dom.fileInput.disabled = state.busy;
    dom.clearFile.disabled = state.busy;
    dom.dropzone.setAttribute("aria-disabled", state.busy ? "true" : "false");
    dom.demoBtn.title = demoReady ? "" : "The sample image is not installed.";
  }

  function populateFontSelect() {
    if (!dom.fontSelect) return;
    var previous = dom.fontSelect.querySelector('optgroup[data-dynamic="fonts"]');
    if (previous) previous.remove();
    if (!state.fontCatalog.length) return;
    var group = document.createElement("optgroup");
    group.label = "Available fonts";
    group.setAttribute("data-dynamic", "fonts");
    state.fontCatalog.forEach(function (item) {
      if (!item || !item.family) return;
      var option = document.createElement("option");
      option.value = "family:" + item.family;
      option.textContent = item.family + (item.category ? " · " + item.category : "");
      group.appendChild(option);
    });
    if (group.children.length) dom.fontSelect.appendChild(group);
  }

  function ensureFontFamilyOption(name) {
    if (!name || !dom.fontSelect) return;
    var value = "family:" + name;
    var exists = Array.prototype.some.call(dom.fontSelect.options, function (option) {
      return option.value === value;
    });
    if (exists) return;
    var group = dom.fontSelect.querySelector('optgroup[data-dynamic="fonts"]');
    if (!group) {
      group = document.createElement("optgroup");
      group.label = "Available fonts";
      group.setAttribute("data-dynamic", "fonts");
      dom.fontSelect.appendChild(group);
    }
    var option = document.createElement("option");
    option.value = value;
    option.textContent = name;
    group.appendChild(option);
  }

  async function loadFontCatalog() {
    try {
      var data = await apiJSON("/api/fonts");
      state.fontCatalog = data && Array.isArray(data.families) ? data.families : [];
      populateFontSelect();
    } catch (error) {
      // The font picker is an enhancement; OCR and rendering still work with
      // the broad sans/serif/mono fallbacks if this request fails.
      console.warn("[InkPatch] could not load font catalogue", error);
    }
  }

  async function refreshHealth(force) {
    var suffix = force ? "?refresh=1" : "";
    setEnginePill("", "Checking engine…", "Checking for Tesseract");
    dom.retryEngine.disabled = true;
    try {
      var results = await Promise.all([
        apiJSON("/api/health" + suffix),
        apiJSON("/api/languages" + suffix)
      ]);
      var health = results[0] || {};
      var languageData = results[1] || {};
      var ocr = health.ocr || languageData || {};
      state.health = health;
      state.languages = Array.isArray(languageData.languages)
        ? languageData.languages
        : (Array.isArray(ocr.languages) ? ocr.languages : []);
      state.engineAvailable = !!(ocr.available && state.languages.length);
      applyHealthLimits(health);
      buildLanguageOptions();

      if (state.engineAvailable) {
        var version = ocr.version || "installed";
        setEnginePill("ready", "Tesseract " + version, [
          ocr.command || "",
          ocr.tessdata ? "tessdata: " + ocr.tessdata : ""
        ].filter(Boolean).join("\n"));
        setEngineWarning(false);
      } else {
        setEnginePill("error", "OCR missing", ocr.hint || "Tesseract is not available.");
        setEngineWarning(
          true,
          "Tesseract is missing",
          "The OCR engine is needed to find text. Install it with your package manager, then check again.",
          false
        );
      }
    } catch (error) {
      state.health = null;
      state.engineAvailable = false;
      setEnginePill("error", "Server offline", "Could not reach the InkPatch API.");
      setEngineWarning(
        true,
        "Server not reachable",
        "Could not reach the InkPatch API. Start the server, then press “Check again”.",
        true
      );
      console.error("[InkPatch] health check failed", error);
    } finally {
      dom.retryEngine.disabled = false;
      updateUploadEnabled();
    }
    return state.engineAvailable;
  }

  /* ------------------------------------------------------------- file pick */

  var selectFile = function (file) {
    if (!file || state.busy) return;
    if (dom.fileInput) dom.fileInput.value = "";
    var name = file.name || "image";
    var extensionMatch = /\.(png|jpe?g|webp|bmp|tiff?)$/i.test(name);
    var typeMatch = /^image\/(png|jpeg|webp|bmp|tiff?)$/i.test(file.type || "");
    if (!extensionMatch && !typeMatch) {
      showToast("Please choose a PNG, JPG, WebP, BMP or TIFF image.", "error");
      return;
    }

    var maxMb = state.health && state.health.limits ? Number(state.health.limits.max_upload_mb) || 20 : 20;
    if (!file.size) {
      showToast("That file is empty.", "error");
      return;
    }
    if (file.size > maxMb * 1024 * 1024) {
      showToast("Images must be under " + maxMb + " MB.", "error");
      return;
    }

    if (state.fileUrl) {
      try { URL.revokeObjectURL(state.fileUrl); } catch (error) { /* ignore */ }
    }
    state.file = file;
    state.fileUrl = URL.createObjectURL(file);

    dom.fileThumb.src = state.fileUrl;
    dom.fileThumb.alt = "Preview of " + name;
    dom.fileName.textContent = name;
    var suffix = (file.type || "").split("/")[1] || (name.split(".").pop() || "image");
    dom.fileDetail.textContent = formatBytes(file.size) + " · " + suffix.toUpperCase();
    dom.filePreview.hidden = false;
    dom.dropzone.hidden = true;
    updateUploadEnabled();
    dom.uploadBtn.focus();
  };

  var clearFile = function () {
    if (state.fileUrl) {
      try { URL.revokeObjectURL(state.fileUrl); } catch (error) { /* ignore */ }
    }
    state.file = null;
    state.fileUrl = null;
    if (dom.fileInput) dom.fileInput.value = "";
    dom.fileThumb.removeAttribute("src");
    dom.filePreview.hidden = true;
    dom.dropzone.hidden = false;
    updateUploadEnabled();
  };

  /* ---------------------------------------------------------------- upload */

  async function startUpload() {
    if (!state.file || !state.engineAvailable || state.busy) return false;
    var lang = dom.langSelect.value || "eng";
    var psm = Number(dom.psmSelect.value) || 3;
    var formData = new FormData();
    formData.append("file", state.file, state.file.name || "image.png");
    formData.append("lang", lang);
    formData.append("psm", String(psm));

    state.busy = true;
    updateUploadEnabled();
    showBusy({
      eyebrow: "Uploading",
      title: "Sending your image",
      text: truncate(state.file.name, 64),
      progress: true
    });

    var ocrPhase = false;
    try {
      var doc = await uploadFile(formData, function (loaded, total) {
        var percent = total ? Math.round((loaded / total) * 100) : 0;
        setBusyProgress(percent);
        if (!ocrPhase && total > 0 && loaded >= total) {
          ocrPhase = true;
          setBusyPhase(
            "Detecting text",
            "Finding the words",
            "Tesseract is mapping lines, colours and type styles…",
            false
          );
        }
      });
      clearFile();
      openDocument(doc);
      if (doc.warning) {
        showToast(doc.warning, "warning", 9000);
      } else {
        showToast("Detected " + plural(doc.line_count || 0, "text line") + ".", "success");
      }
      return true;
    } catch (error) {
      handleError(error, "upload");
      return false;
    } finally {
      state.busy = false;
      hideBusy();
      updateUploadEnabled();
    }
  }

  async function startDemo() {
    if (!state.engineAvailable || state.busy) return false;
    state.busy = true;
    updateUploadEnabled();
    showBusy({
      eyebrow: "Sample poster",
      title: "Reading the coffee poster",
      text: "Tesseract is finding lines and sampling their type styles…"
    });
    try {
      var doc = await apiJSON("/api/demo", { method: "POST" });
      openDocument(doc);
      if (doc.warning) {
        showToast(doc.warning, "warning", 9000);
      } else {
        showToast("Demo ready. Click a highlighted line to edit it.", "success", 6000);
      }
      return true;
    } catch (error) {
      handleError(error, "demo");
      return false;
    } finally {
      state.busy = false;
      hideBusy();
      updateUploadEnabled();
    }
  }

  /* ------------------------------------------------------------ view state */

  function showEditorView() {
    state.view = "editor";
    dom.landingView.hidden = true;
    dom.editorView.hidden = false;
    dom.newImageBtn.hidden = false;
    dom.downloadBtn.hidden = false;
    document.body.classList.add("editor-open");
    closeDrawers();
    window.scrollTo(0, 0);
    if (state.doc) document.title = state.doc.name + " — InkPatch";
  }

  function resetEditorState() {
    cancelPreviewTimer();
    clearPendingRetry();
    state.imageToken += 1;
    state.lines = [];
    state.lineIndex = new Map();
    state.edits = new Map();
    state.listNodes = new Map();
    state.selectedId = null;
    state.hoverId = null;
    state.addingText = false;
    state.filter = "all";
    state.search = "";
    state.zoomMode = "fit";
    state.scale = 1;
    state.needFit = true;
    state.previewBusy = false;
    state.previewQueued = false;
    state.renderPromise = null;
    state.renderFailed = false;
    state.renderedSignature = "";
    state.previewVersion = 0;
    state.previewAt = null;
    state.downloadedSignature = null;
    state.downloading = false;

    dom.lineList.textContent = "";
    dom.lineList.scrollTop = 0;
    dom.lineSearch.value = "";
    dom.searchClear.hidden = true;
    dom.editForm.hidden = true;
    dom.editEmpty.hidden = false;
    dom.docImage.removeAttribute("src");
    dom.imageWrap.style.width = "";
    dom.imageWrap.style.height = "";
    dom.stageLoader.hidden = true;
    dom.stageFloatHint.hidden = true;
    if (state.stageHintTimer) {
      window.clearTimeout(state.stageHintTimer);
      state.stageHintTimer = null;
    }
    dom.lineEmpty.hidden = true;
    dom.editText.value = "";
    dom.colorPicker.value = "#22241f";
    dom.colorHex.value = "#22241f";
    dom.scaleRange.value = "100";
    dom.scaleOutput.textContent = "100%";
    dom.fontSelect.value = "auto";
    dom.weightSelect.value = "auto";
    dom.alignModes.forEach(function (input) {
      input.checked = input.value === "auto";
    });
    dom.filterTabs.forEach(function (tab) {
      var active = tab.getAttribute("data-filter") === "all";
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    dom.filterAllCount.textContent = "0";
    dom.filterEditedCount.textContent = "0";
    dom.dotAllBtn.disabled = true;
    dom.copyAllBtn.disabled = true;
    dom.copyLineBtn.disabled = true;
    if (dom.copyStyleBtn) dom.copyStyleBtn.disabled = true;
    if (dom.styleSourceSelect) {
      dom.styleSourceSelect.textContent = "";
      dom.styleSourceSelect.disabled = true;
    }
    if (dom.addTextBtn) dom.addTextBtn.classList.remove("is-active");
    if (dom.overlay) dom.overlay.classList.remove("is-adding-text");
    dom.linesBadge.textContent = "0";
    dom.dirtyReadout.textContent = "0";
    dom.previewReadout.textContent = "Showing original";
    dom.zoomValue.textContent = "Fit";
    dom.zoomReadout.textContent = "Fit";
    dom.stageInfo.textContent = "Hover a box to inspect it";

    var context = dom.overlay.getContext("2d");
    if (context) context.clearRect(0, 0, dom.overlay.width, dom.overlay.height);
  }

  function showLandingView() {
    state.view = "landing";
    dom.landingView.hidden = false;
    dom.editorView.hidden = true;
    dom.newImageBtn.hidden = true;
    dom.downloadBtn.hidden = true;
    document.body.classList.remove("editor-open");
    closeDrawers();
    document.title = "InkPatch — Edit text in images without breaking the design";
    window.scrollTo(0, 0);
  }

  function releaseDocument() {
    var doc = state.doc;
    state.doc = null;
    if (!doc || !doc.id) return;
    try {
      fetch("/api/documents/" + encodeURIComponent(doc.id), {
        method: "DELETE",
        credentials: "same-origin",
        keepalive: true
      }).catch(function () { /* The TTL cleaner will handle it. */ });
    } catch (error) {
      /* ignore */
    }
  }

  async function leaveEditor(options) {
    options = options || {};
    if (!state.doc) {
      showLandingView();
      return;
    }
    var signature = currentSignature();
    var unsaved = signature && signature !== state.downloadedSignature;
    if (unsaved) {
      var ok = await confirmAction({
        title: options.title || "Leave this image?",
        text: options.text || "Your current edits have not been downloaded. They will be discarded.",
        okLabel: options.okLabel || "Discard edits",
        cancelLabel: "Keep editing",
        danger: true
      });
      if (!ok) return;
    }
    releaseDocument();
    resetEditorState();
    showLandingView();
  }

  function abandonEditor(removeFromServer) {
    if (removeFromServer) {
      releaseDocument();
    } else {
      state.doc = null;
    }
    resetEditorState();
    showLandingView();
  }

  function handleError(error, context) {
    console.error("[InkPatch] " + context, error);
    var message = error && error.message ? error.message : "Something went wrong.";
    if (error instanceof ApiError && error.status === 503) {
      showToast(message, "error", 10000);
      refreshHealth(true);
      return;
    }
    if (error instanceof ApiError && error.status === 404 && state.doc &&
        (context === "render" || context === "download" || context === "rescan")) {
      showToast("This document expired or was removed from the server.", "error", 9000);
      abandonEditor(false);
      return;
    }
    // One coalesced notice replaces a failed request. It is never followed by
    // an automatic retry, so a burst of edits cannot flood the server.
    if (error instanceof ApiError && error.status === 0) {
      queueRetry(context);
      return;
    }
    showToast(message, "error", context === "upload" ? 9000 : 7500);
  }

  /* ---------------------------------------------------------------- edits */

  function defaultEdit(line) {
    var style = styleOf(line);
    return {
      text: normalizeText(line.text),
      colorMode: "original",
      color: normalizeHex(style.color) || "#22241f",
      font: "auto",
      fontFamily: "",
      weight: "auto",
      underline: "auto",
      strikethrough: "auto",
      shadow: "auto",
      backgroundMode: "original",
      background: normalizeHex(style.background) || "#f5f2e8",
      backgroundTone: 50,
      align: "auto",
      scale: 1,
      fontSize: null
    };
  }

  function effectOverride(value) {
    if (value === "on") return true;
    if (value === "off") return false;
    return null;
  }

  function effectiveBackground(edit) {
    return shadeHex(edit.background, edit.backgroundTone) ||
      normalizeHex(edit.background) ||
      "#f5f2e8";
  }

  function ensureEdit(line) {
    var edit = state.edits.get(line.id);
    if (!edit) {
      edit = defaultEdit(line);
      state.edits.set(line.id, edit);
    }
    return edit;
  }

  function visibleStyleOf(line) {
    var style = Object.assign({}, styleOf(line));
    var edit = state.edits.get(line.id);
    if (!edit) return style;
    if (edit.colorMode === "custom") {
      style.color = normalizeHex(edit.color) || style.color;
    }
    if (edit.fontFamily) {
      style.font_name = edit.fontFamily;
    } else if (edit.font && edit.font !== "auto") {
      style.font = edit.font;
      style.font_name = "";
    }
    if (edit.weight && edit.weight !== "auto") {
      style.bold = edit.weight === "bold" || edit.weight === "bold-italic";
      style.italic = edit.weight === "italic" || edit.weight === "bold-italic";
    }
    if (edit.underline !== "auto") style.underline = edit.underline === "on";
    if (edit.strikethrough !== "auto") style.strikethrough = edit.strikethrough === "on";
    if (edit.shadow !== "auto") {
      style.shadow = edit.shadow === "on" ? { dx: 1, dy: 1 } : null;
    }
    if (edit.align !== "auto") style.align = edit.align;
    if (edit.fontSize != null && isFinite(Number(edit.fontSize))) {
      style.font_size = Number(edit.fontSize);
    } else if (Number(edit.scale) !== 1 && Number(style.font_size) > 0) {
      style.font_size = Number(style.font_size) * Number(edit.scale);
    }
    return style;
  }

  function syncStyleSources(line) {
    if (!dom.styleSourceSelect) return;
    var previous = dom.styleSourceSelect.value;
    dom.styleSourceSelect.textContent = "";
    var placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = state.lines.length > 1
      ? "Choose a selection…"
      : "No other selections";
    dom.styleSourceSelect.appendChild(placeholder);
    state.lines.forEach(function (item) {
      if (!item || !item.id || item.id === line.id) return;
      var option = document.createElement("option");
      option.value = item.id;
      var text = currentLineText(item).trim();
      option.textContent = item.id + " · " + (text ? truncate(text, 36) : "empty");
      dom.styleSourceSelect.appendChild(option);
    });
    if (previous && previous !== line.id && state.lineIndex.has(previous)) {
      dom.styleSourceSelect.value = previous;
    }
    dom.styleSourceSelect.disabled = state.lines.length <= 1;
    if (dom.copyStyleBtn) dom.copyStyleBtn.disabled = !dom.styleSourceSelect.value;
  }

  function copyStyleFromSource() {
    var target = selectedLine();
    if (!target) return;
    var sourceId = dom.styleSourceSelect.value;
    var source = sourceId ? state.lineIndex.get(sourceId) : null;
    if (!source || source === target) {
      showToast("Choose another line to copy the style from.", "info", 3000);
      return;
    }
    var sourceStyle = visibleStyleOf(source);
    var edit = ensureEdit(target);
    var family = String(sourceStyle.font_name || "").trim();
    edit.fontFamily = family;
    edit.font = family ? "auto" : (
      sourceStyle.font === "serif" || sourceStyle.font === "mono" ? sourceStyle.font : "sans"
    );
    edit.weight = sourceStyle.bold && sourceStyle.italic
      ? "bold-italic"
      : sourceStyle.bold
        ? "bold"
        : sourceStyle.italic
          ? "italic"
          : "regular";
    var color = normalizeHex(sourceStyle.color);
    if (color) {
      edit.color = color;
      edit.colorMode = "custom";
    }
    edit.align = sourceStyle.align === "left" ||
      sourceStyle.align === "center" ||
      sourceStyle.align === "right"
      ? sourceStyle.align
      : "auto";
    var size = Number(sourceStyle.font_size);
    if (isFinite(size) && size > 0) {
      edit.fontSize = clamp(size, 4, 800);
      edit.scale = 1;
    }
    edit.underline = sourceStyle.underline ? "on" : "off";
    edit.strikethrough = sourceStyle.strikethrough ? "on" : "off";
    edit.shadow = sourceStyle.shadow ? "on" : "off";
    // The background is intentionally not copied: a fill belongs to one box.
    syncInspector(target);
    afterEditChange(target, 320);
    showToast(
      "Style copied from " + source.id + ". Background was not copied.",
      "success",
      3400
    );
  }

  function nextNewLineId() {
    var index = 1;
    while (state.lineIndex.has("N" + ("00" + index).slice(-3))) index += 1;
    return "N" + ("00" + index).slice(-3);
  }

  function newBoxAt(x, y) {
    if (!state.doc) return null;
    var width = Number(state.doc.width) || 0;
    var height = Number(state.doc.height) || 0;
    if (width < 8 || height < 8) return null;
    var boxHeight = clamp(Math.round(height * 0.05), 20, 96);
    var boxWidth = clamp(Math.round(width * 0.28), boxHeight * 3, 720);
    boxWidth = Math.min(boxWidth, width - 8);
    boxHeight = Math.min(boxHeight, height - 8);
    var left = clamp(Math.round(x), 4, Math.max(4, width - boxWidth - 4));
    var top = clamp(Math.round(y - boxHeight / 2), 4, Math.max(4, height - boxHeight - 4));
    return {
      x: left,
      y: top,
      width: Math.max(12, Math.round(boxWidth)),
      height: Math.max(12, Math.round(boxHeight))
    };
  }

  function removeNewLine(line) {
    if (!line || !line.isNew) return;
    cancelPreviewTimer();
    state.edits.delete(line.id);
    state.lines = state.lines.filter(function (item) { return item !== line; });
    state.lineIndex.delete(line.id);
    state.listNodes.delete(line.id);
    if (state.selectedId === line.id) {
      state.selectedId = null;
      dom.editForm.hidden = true;
      dom.editEmpty.hidden = false;
      dom.editText.value = "";
      dom.editOriginal.textContent = "";
    }
    buildLineList();
    updatePreviewUI();
    drawOverlay();
    updateStageInfo();
    syncCopyButtons();
    schedulePreview(180);
    showToast("New text box removed.", "info", 2600);
  }

  function addTextAt(x, y) {
    if (!state.doc) return;
    var box = newBoxAt(x, y);
    if (!box) return;
    var id = nextNewLineId();
    var fontSize = Math.max(6, Math.round(box.height * 0.62));
    var line = {
      id: id,
      text: "",
      box: box,
      confidence: 100,
      words: [],
      mask_boxes: [],
      isNew: true,
      style: {
        color: "#22241f",
        background: null,
        font: "sans",
        font_name: "",
        font_size: fontSize,
        bold: false,
        italic: false,
        align: "left",
        underline: false,
        strikethrough: false,
        shadow: null
      }
    };
    state.lines.push(line);
    state.lineIndex.set(id, line);
    buildLineList();
    setAddTextMode(false);
    selectLine(id, { openMobile: true, scroll: true });
    dom.editText.focus();
    showToast("New text box added. Type the text you want to draw.", "success", 3000);
  }

  function setAddTextMode(on) {
    state.addingText = !!on && !!state.doc;
    if (dom.addTextBtn) {
      dom.addTextBtn.classList.toggle("is-active", state.addingText);
      dom.addTextBtn.setAttribute("aria-pressed", state.addingText ? "true" : "false");
    }
    if (dom.overlay) dom.overlay.classList.toggle("is-adding-text", state.addingText);
    if (!dom.overlay) return;
    if (state.addingText) {
      showStageHint("Click on the image to place a new text box. Press Esc to cancel.", 0);
      dom.overlay.style.cursor = "text";
    } else {
      hideStageHint();
      dom.overlay.style.cursor = "crosshair";
      updateStageInfo();
    }
  }

  function isDirty(edit, line) {
    if (!edit) return false;
    var style = styleOf(line);
    var originalColor = normalizeHex(style.color) || "";
    var customColor = normalizeHex(edit.color) || "";
    var colorDirty = edit.colorMode === "custom" && customColor && customColor !== originalColor;
    return normalizeText(edit.text) !== normalizeText(line.text) ||
      !!colorDirty ||
      edit.font !== "auto" ||
      !!edit.fontFamily ||
      edit.weight !== "auto" ||
      edit.underline !== "auto" ||
      edit.strikethrough !== "auto" ||
      edit.shadow !== "auto" ||
      edit.backgroundMode === "custom" ||
      edit.align !== "auto" ||
      edit.fontSize != null ||
      Number(edit.scale) !== 1;
  }

  function toPayload(edit, line) {
    var weight = edit.weight || "auto";
    var payload = {
      line_id: line.id,
      text: normalizeText(edit.text),
      color: edit.colorMode === "custom" ? normalizeHex(edit.color) : null,
      font: edit.font,
      font_family: edit.fontFamily || null,
      align: edit.align,
      scale: Number(edit.scale) || 1,
      font_size: edit.fontSize != null && isFinite(Number(edit.fontSize))
        ? Number(edit.fontSize)
        : null,
      bold: weight === "auto" ? null : weight === "bold" || weight === "bold-italic",
      italic: weight === "auto" ? null : weight === "italic" || weight === "bold-italic",
      underline: effectOverride(edit.underline),
      strikethrough: effectOverride(edit.strikethrough),
      shadow: effectOverride(edit.shadow),
      background: edit.backgroundMode === "custom" ? effectiveBackground(edit) : null
    };
    if (line.isNew && line.box) {
      // A box on a new line tells the server to draw over the source pixels
      // instead of erasing an OCR line.  Existing lines never send one.
      payload.box = {
        x: Number(line.box.x) || 0,
        y: Number(line.box.y) || 0,
        width: Math.max(1, Number(line.box.width) || 1),
        height: Math.max(1, Number(line.box.height) || 1)
      };
    }
    return payload;
  }

  function dirtyEdits() {
    var edits = [];
    var seen = new Set();
    state.lines.forEach(function (line) {
      if (!line || !line.id || seen.has(line.id)) return;
      seen.add(line.id);
      var edit = state.edits.get(line.id);
      if (edit && isDirty(edit, line)) edits.push(toPayload(edit, line));
    });
    return edits;
  }

  function currentLineText(line) {
    if (!line) return "";
    var edit = state.edits.get(line.id);
    return normalizeText(edit ? edit.text : line.text);
  }

  function imageTextLines() {
    var seen = new Set();
    var lines = [];
    state.lines.forEach(function (line) {
      if (!line || !line.id || seen.has(line.id)) return;
      seen.add(line.id);
      var text = currentLineText(line);
      if (text.trim()) lines.push(text);
    });
    return lines;
  }

  function syncCopyButtons() {
    var line = selectedLine();
    var text = currentLineText(line);
    var hasText = !!text.trim();
    if (dom.copyLineBtn) {
      dom.copyLineBtn.disabled = !hasText;
      dom.copyLineBtn.title = hasText
        ? "Copy " + line.id + " text to the clipboard"
        : "The selected line has no text to copy";
      dom.copyLineBtn.setAttribute(
        "aria-label",
        hasText ? "Copy " + line.id + " text to the clipboard" : "Copy selected line text"
      );
    }
    var hasImageText = imageTextLines().length > 0;
    if (dom.copyAllBtn) {
      dom.copyAllBtn.disabled = !hasImageText;
      dom.copyAllBtn.title = hasImageText
        ? "Copy all text in this image"
        : "There is no text in this image to copy";
    }
  }

  function copySelectedLineText() {
    var line = selectedLine();
    if (!line) return;
    var text = currentLineText(line);
    if (!text.trim()) {
      showToast("The selected line has no text to copy.", "info", 2600);
      return;
    }
    copyToClipboard(text).then(function (copied) {
      if (copied) {
        showToast("Copied " + line.id + " text to the clipboard.", "success", 2600);
      } else {
        showToast("Could not copy the line. Select its text and copy manually.", "error", 5000);
      }
    });
  }

  function copyAllImageText() {
    var lines = imageTextLines();
    if (!lines.length) {
      showToast("There is no text in this image to copy.", "info", 2600);
      return;
    }
    copyToClipboard(lines.join("\n")).then(function (copied) {
      if (copied) {
        showToast("Copied all text from " + plural(lines.length, "line") + ".", "success", 2600);
      } else {
        showToast("Could not copy the image text. Select the lines and copy manually.", "error", 5000);
      }
    });
  }

  function appendDotToAllLines() {
    if (!state.doc || !state.lines.length) return;
    var changed = 0;
    var seen = new Set();
    state.lines.forEach(function (line) {
      if (!line || !line.id || seen.has(line.id)) return;
      seen.add(line.id);
      var edit = ensureEdit(line);
      edit.text = normalizeText(edit.text) + ".";
      changed += 1;
      refreshLineItem(line);
    });
    if (!changed) return;
    var line = selectedLine();
    if (line) syncInspector(line);
    syncCopyButtons();
    updatePreviewUI();
    cancelPreviewTimer();
    showToast(
      "Appended “.” to " + plural(changed, "line") + ". Rendering preview…",
      "success",
      3400
    );
    renderCurrent({ force: true });
  }

  function signatureOf(edits) {
    return edits.length ? JSON.stringify(edits) : "";
  }

  function currentSignature() {
    return signatureOf(dirtyEdits());
  }

  function cancelPreviewTimer() {
    if (state.renderTimer) {
      window.clearTimeout(state.renderTimer);
      state.renderTimer = null;
    }
  }

  function schedulePreview(delay) {
    cancelPreviewTimer();
    if (!state.doc) return;
    updatePreviewUI();
    if (!state.livePreview) return;
    // After a network failure the latest edits are kept locally. Do not fire a
    // request per keystroke or line; the single Retry all action sends them.
    if (hasPendingRetry("render")) {
      state.previewQueued = false;
      updateRetryNotice();
      return;
    }
    if (state.previewBusy) {
      state.previewQueued = true;
      return;
    }
    state.renderTimer = window.setTimeout(function () {
      state.renderTimer = null;
      renderCurrent();
    }, typeof delay === "number" ? delay : 550);
  }

  function updatePreviewUI() {
    if (!state.doc || state.view !== "editor") return;
    var edits = dirtyEdits();
    var count = edits.length;
    var signature = signatureOf(edits);
    var current = signature === state.renderedSignature;
    var statusKind = "";
    var statusText = "Showing original";
    var readout = "Showing original";

    if (state.previewBusy) {
      statusKind = "is-busy";
      statusText = "Rendering preview…";
      readout = "Rendering…";
    } else if (state.renderFailed) {
      statusKind = "is-error";
      statusText = hasPendingRetry("render") ? "Not sent - retry all" : "Preview failed";
      readout = hasPendingRetry("render") ? "Waiting to retry" : "Preview failed";
    } else if (count > 0 && !current) {
      statusKind = "is-dirty";
      statusText = state.livePreview ? "Waiting to render…" : "Unrendered changes";
      readout = "Unrendered changes";
    } else if (count > 0 && current) {
      statusKind = "is-ready";
      statusText = "Preview updated";
      readout = "Preview updated" + (state.previewAt ? " · " + formatClock(state.previewAt) : "");
    } else if (state.doc.has_preview) {
      statusText = "Original restored";
      readout = "Showing original";
    }

    dom.renderStatus.className = "render-status" + (statusKind ? " " + statusKind : "");
    dom.renderStatusText.textContent = statusText;
    dom.dirtyReadout.textContent = String(count);
    dom.previewReadout.textContent = readout;
    // Reverting an edit while live preview is off leaves a rendered preview on
    // screen with no dirty lines.  Keep a button available so the original can
    // be restored instead of forcing a download to refresh the stage.
    var canClear = count === 0 && !!state.doc.has_preview && state.renderedSignature !== "";
    dom.previewBtn.hidden = !(
      (count > 0 && !current && (!state.livePreview || state.renderFailed)) ||
      canClear
    );
    dom.previewBtn.disabled = state.previewBusy;
    dom.dotAllBtn.disabled = !state.lines.length || state.previewBusy;
    var previewLabel = dom.previewBtn.querySelector("span");
    if (previewLabel) {
      previewLabel.textContent = canClear
        ? "Restore original"
        : state.renderFailed ? "Retry all changes" : "Update preview";
    }
    applyFilters();
  }

  function swapStageImage(url) {
    return new Promise(function (resolve, reject) {
      var probe = new Image();
      probe.onload = function () {
        if (!state.doc) {
          resolve();
          return;
        }
        state.needFit = false;
        dom.docImage.src = url;
        resolve();
      };
      probe.onerror = function () {
        reject(new ApiError("The rendered preview image could not be loaded.", 0));
      };
      probe.src = url;
    });
  }

  function renderCurrent(options) {
    options = options || {};
    if (!state.doc) return Promise.resolve(false);

    if (state.renderPromise) {
      return state.renderPromise.then(function (done) {
        if (!state.doc) return false;
        if (options.force || currentSignature() !== state.renderedSignature) {
          return renderCurrent(options);
        }
        return done;
      });
    }

    var edits = dirtyEdits();
    var signature = signatureOf(edits);
    var needClear = edits.length === 0;

    if (!options.force && signature === state.renderedSignature) {
      updatePreviewUI();
      return Promise.resolve(true);
    }
    if (needClear && state.renderedSignature === "" && !state.doc.has_preview) {
      updatePreviewUI();
      return Promise.resolve(true);
    }

    var docId = state.doc.id;
    state.previewBusy = true;
    state.renderFailed = false;
    updatePreviewUI();

    state.renderPromise = (async function () {
      try {
        var result = await apiJSON("/api/documents/" + encodeURIComponent(docId) + "/render", {
          method: "POST",
          body: JSON.stringify({ edits: edits })
        });
        if (!state.doc || state.doc.id !== docId) return false;
        var version = Number(result && result.preview_version) || 0;
        var url = result && result.preview_url
          ? result.preview_url
          : "/api/documents/" + encodeURIComponent(docId) + "/preview?v=" + version;
        await swapStageImage(url);
        if (!state.doc || state.doc.id !== docId) return false;
        state.previewVersion = version;
        state.doc.has_preview = true;
        state.doc.preview_version = version;
        state.renderedSignature = needClear ? "" : signature;
        state.previewAt = needClear ? null : new Date();
        state.renderFailed = false;
        resolveRetry("render");
        return true;
      } catch (error) {
        if (state.doc && state.doc.id === docId) {
          state.renderFailed = true;
          if (!options.quiet) handleError(error, "render");
        }
        return false;
      } finally {
        if (state.doc && state.doc.id === docId) {
          state.previewBusy = false;
          state.renderPromise = null;
          updatePreviewUI();
          if (state.previewQueued) {
            state.previewQueued = false;
            if (state.livePreview && !state.renderFailed) schedulePreview(90);
          } else if (state.livePreview && !state.renderFailed &&
                     currentSignature() !== state.renderedSignature) {
            schedulePreview(250);
          }
        } else {
          state.previewBusy = false;
          state.renderPromise = null;
        }
      }
    })();

    return state.renderPromise;
  }

  /* ----------------------------------------------------------- open a doc */

  function openDocument(doc, options) {
    options = options || {};
    if (!doc || !doc.id) {
      showToast("The server returned an empty document.", "error");
      return;
    }

    clearPendingRetry();
    var wasEditor = state.view === "editor";
    state.doc = doc;
    state.lines = Array.isArray(doc.lines) ? doc.lines.slice() : [];
    state.lineIndex = new Map();
    state.lines.forEach(function (line) { state.lineIndex.set(line.id, line); });
    state.edits = new Map();
    state.selectedId = null;
    state.hoverId = null;
    state.addingText = false;
    state.filter = "all";
    state.search = "";
    state.zoomMode = "fit";
    state.scale = 1;
    state.needFit = true;
    state.previewBusy = false;
    state.previewQueued = false;
    state.renderPromise = null;
    state.renderFailed = false;
    state.renderedSignature = "";
    state.previewVersion = Number(doc.preview_version) || 0;
    state.previewAt = null;
    state.downloadedSignature = null;
    state.downloading = false;

    dom.docName.textContent = doc.name || "Untitled image";
    dom.docSize.textContent = (doc.width || "?") + " × " + (doc.height || "?") + " px";
    dom.docLineCount.textContent = plural(doc.line_count != null ? doc.line_count : state.lines.length, "line");
    dom.docLanguage.textContent = languageLabel(doc.lang);
    dom.docFile.textContent = doc.name || "Untitled";
    dom.docDims.textContent = (doc.width || "?") + " × " + (doc.height || "?") + " px";
    dom.docLines.textContent = plural(state.lines.length, "text line");
    dom.docEngine.textContent = doc.engine_version ? "Tesseract " + doc.engine_version : "Unknown";
    dom.docWarning.hidden = !doc.warning;
    dom.docWarningText.textContent = doc.warning || "";
    setSelectValue(dom.docLang, doc.lang || "eng");
    dom.docPsm.value = String(doc.psm || 3);

    dom.lineSearch.value = "";
    dom.searchClear.hidden = true;
    dom.lineEmpty.hidden = true;
    dom.lineList.textContent = "";
    dom.listNodes = new Map();
    state.listNodes = new Map();

    showEditorView();
    setAddTextMode(false);
    buildLineList();
    updatePreviewUI();
    dom.docImage.removeAttribute("src");
    dom.imageWrap.style.width = "";
    dom.imageWrap.style.height = "";
    dom.stageLoader.hidden = false;
    dom.stageInfo.textContent = "Hover a box to inspect it";
    if (doc.warning) {
      showStageHint(doc.warning, 10000);
    } else if (!state.lines.length) {
      showStageHint("No text was detected. Try another language or a sparse layout in Document settings.", 0);
    } else {
      hideStageHint();
    }

    if (state.file) clearFile();

    var token = ++state.imageToken;
    // The working image can change after a rescan of an older document (it is
    // straightened in place), so include the token to bypass the image cache.
    dom.docImage.src = "/api/documents/" + encodeURIComponent(doc.id) + "/image?v=" + token;
    dom.docImage.setAttribute("data-doc-token", String(token));

    if (state.lines.length && window.innerWidth > 1100 && options.selectFirst !== false) {
      selectLine(state.lines[0].id, { openMobile: false, scroll: false });
    } else {
      deselectLine();
      if (!state.lines.length) setInspectorTab("document");
    }

    window.requestAnimationFrame(function () {
      if (state.view !== "editor" || !state.doc || state.doc.id !== doc.id) return;
      if (state.needFit) {
        state.needFit = false;
        applyZoom("fit");
      }
    });

    if (!wasEditor) window.scrollTo(0, 0);
    updatePreviewUI();
  }

  /* ------------------------------------------------------------ line list */

  function faceLabel(line) {
    return faceSummary(styleOf(line));
  }

  function buildLineList() {
    dom.lineList.textContent = "";
    state.listNodes = new Map();
    var fragment = document.createDocumentFragment();

    state.lines.forEach(function (line, index) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "line-item";
      button.setAttribute("data-id", line.id);

      var indexNode = document.createElement("span");
      indexNode.className = "line-index";
      indexNode.textContent = ("000" + (index + 1)).slice(-3);

      var copy = document.createElement("span");
      copy.className = "line-copy";

      var textNode = document.createElement("span");
      textNode.className = "line-text";

      var sub = document.createElement("span");
      sub.className = "line-sub";

      var dot = document.createElement("span");
      dot.className = "conf-dot " + confidenceLevel(line.confidence);

      var confidence = document.createElement("span");
      confidence.textContent = Math.round(Number(line.confidence) || 0) + "%";

      var face = document.createElement("span");
      face.className = "line-face";
      face.textContent = faceLabel(line);

      var mark = document.createElement("span");
      mark.className = "edit-mark";
      mark.textContent = "edited";
      mark.hidden = true;

      sub.appendChild(dot);
      sub.appendChild(confidence);
      sub.appendChild(face);
      sub.appendChild(mark);
      copy.appendChild(textNode);
      copy.appendChild(sub);
      button.appendChild(indexNode);
      button.appendChild(copy);

      button.addEventListener("click", function () {
        selectLine(line.id, { openMobile: true, scroll: false, revealStage: true });
      });
      button.addEventListener("mouseenter", function () {
        state.hoverId = line.id;
        drawOverlay();
        showLineInStage(line);
      });
      button.addEventListener("mouseleave", function () {
        if (state.hoverId === line.id) {
          state.hoverId = null;
          drawOverlay();
          updateStageInfo();
        }
      });
      button.addEventListener("keydown", function (event) {
        var keys = ["ArrowDown", "ArrowUp", "Home", "End"];
        if (keys.indexOf(event.key) === -1) return;
        event.preventDefault();
        var items = visibleLineButtons();
        var current = items.indexOf(button);
        if (current === -1) return;
        var next = current;
        if (event.key === "ArrowDown") next = Math.min(items.length - 1, current + 1);
        if (event.key === "ArrowUp") next = Math.max(0, current - 1);
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = items.length - 1;
        var target = items[next];
        if (target) {
          target.focus();
          target.click();
        }
      });

      fragment.appendChild(button);
      state.listNodes.set(line.id, {
        button: button,
        text: textNode,
        confidence: confidence,
        dot: dot,
        face: face,
        mark: mark
      });
    });

    dom.lineList.appendChild(fragment);
    state.lines.forEach(refreshLineItem);
    applyFilters();
  }

  function refreshLineItem(line) {
    var node = state.listNodes.get(line.id);
    if (!node) return;
    var edit = state.edits.get(line.id);
    var dirty = !!(edit && isDirty(edit, line));
    var text = edit ? normalizeText(edit.text) : normalizeText(line.text);
    var empty = !text.trim();
    var display = empty ? (dirty ? "Text removed" : "Empty line") : text;

    node.text.textContent = display;
    node.confidence.textContent = Math.round(Number(line.confidence) || 0) + "%";
    node.dot.className = "conf-dot " + confidenceLevel(line.confidence);
    node.face.textContent = faceLabel(line);
    node.mark.hidden = !dirty;
    node.button.classList.toggle("is-edited", dirty);
    node.button.classList.toggle("is-removed", dirty && empty);
    node.button.classList.toggle("is-selected", state.selectedId === line.id);
    node.button.setAttribute("aria-label",
      line.id + ": " + display + ", confidence " + Math.round(Number(line.confidence) || 0) + "%");
    node.button.title = display + " · " + faceLabel(line);
  }

  function refreshLineSelection() {
    state.lines.forEach(function (line) {
      var node = state.listNodes.get(line.id);
      if (node) node.button.classList.toggle("is-selected", state.selectedId === line.id);
    });
  }

  function visibleLineButtons() {
    return $$(".line-item:not(.is-hidden)", dom.lineList);
  }

  function applyFilters() {
    var query = state.search.trim().toLowerCase();
    var edited = 0;
    var visible = 0;

    state.lines.forEach(function (line) {
      var edit = state.edits.get(line.id);
      var dirty = !!(edit && isDirty(edit, line));
      if (dirty) edited += 1;
      var text = normalizeText(edit ? edit.text : line.text).toLowerCase();
      var matches = !query || text.indexOf(query) !== -1 || line.id.toLowerCase().indexOf(query) !== -1;
      var shown = matches && (state.filter === "all" || dirty);
      var node = state.listNodes.get(line.id);
      if (node) node.button.classList.toggle("is-hidden", !shown);
      if (shown) visible += 1;
    });

    dom.filterAllCount.textContent = String(state.lines.length);
    dom.filterEditedCount.textContent = String(edited);
    dom.linesBadge.textContent = String(state.lines.length);
    dom.searchClear.hidden = !state.search;

    dom.filterTabs.forEach(function (tab) {
      var active = tab.getAttribute("data-filter") === state.filter;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });

    dom.lineEmpty.hidden = visible > 0;
    if (visible === 0) {
      if (!state.lines.length) {
        dom.lineEmptyText.textContent = "No text was detected in this image. Try another language or layout in Document settings.";
      } else if (query) {
        dom.lineEmptyText.textContent = "No lines match “" + truncate(query, 40) + "”.";
      } else if (state.filter === "edited") {
        dom.lineEmptyText.textContent = "Nothing has been edited yet. Select a line and change its text or style.";
      } else {
        dom.lineEmptyText.textContent = "No lines to show.";
      }
    }
  }

  function setFilter(filter) {
    state.filter = filter === "edited" ? "edited" : "all";
    applyFilters();
  }

  function scrollLineIntoView(lineId) {
    var node = state.listNodes.get(lineId);
    if (!node || node.button.classList.contains("is-hidden")) return;
    try {
      node.button.scrollIntoView({
        block: "nearest",
        behavior: prefersReducedMotion() ? "auto" : "smooth"
      });
    } catch (error) {
      node.button.scrollIntoView(false);
    }
  }

  /* -------------------------------------------------------------- selection */

  var mobileQuery = window.matchMedia ? window.matchMedia("(max-width: 1100px)") : null;

  function isMobileLayout() {
    return !!(mobileQuery && mobileQuery.matches);
  }

  function selectedLine() {
    return state.selectedId ? state.lineIndex.get(state.selectedId) || null : null;
  }

  function lineIsVisible(line) {
    var query = state.search.trim().toLowerCase();
    var edit = state.edits.get(line.id);
    var dirty = !!(edit && isDirty(edit, line));
    var text = normalizeText(edit ? edit.text : line.text).toLowerCase();
    var matches = !query || text.indexOf(query) !== -1 || line.id.toLowerCase().indexOf(query) !== -1;
    return matches && (state.filter === "all" || dirty);
  }

  function updateFontHint(edit, line) {
    var style = styleOf(line);
    var parts = [];
    var effects = [];
    var backgroundFilled = edit.backgroundMode === "custom";
    if (edit.underline !== "auto") effects.push("underline " + edit.underline);
    if (edit.strikethrough !== "auto") effects.push("strike " + edit.strikethrough);
    if (edit.shadow !== "auto") effects.push("shadow " + edit.shadow);
    if (backgroundFilled) effects.push("background " + effectiveBackground(edit));
    if (line.isNew) {
      var choice = edit.fontFamily
        ? edit.fontFamily
        : familyName(edit.font !== "auto" ? edit.font : style.font);
      parts.push("New text box — " + choice);
      var customSize = Number(edit.fontSize);
      var baseSize = customSize > 0 ? customSize : Number(style.font_size);
      if (baseSize > 0) parts.push(Math.round(baseSize) + " px");
      if (edit.align !== "auto") parts.push(edit.align + "-aligned");
      if (effects.length) parts.push("effects: " + effects.join(", "));
      else parts.push("choose a typeface, or copy style from another selection");
      dom.fontHint.textContent = parts.join(" — ") + ".";
      return;
    }
    if (style.font || style.font_name) {
      var detected = faceSummary(style);
      var size = Number(style.font_size);
      if (size > 0) detected += " · ~" + Math.round(size) + " px";
      if (style.align && style.align !== "left") detected += " · " + style.align + "-aligned";
      parts.push("Detected: " + detected);
    } else {
      parts.push("No type sample — Sans is the fallback");
    }
    if (edit.fontFamily) {
      parts.push("using " + edit.fontFamily);
    } else if (
      edit.font !== "auto" ||
      edit.weight !== "auto" ||
      edit.align !== "auto" ||
      Number(edit.scale) !== 1 ||
      backgroundFilled ||
      effects.length
    ) {
      parts.push("your override is active");
    } else {
      parts.push("matching keeps the original look");
    }
    if (effects.length) parts.push("effects: " + effects.join(", "));
    dom.fontHint.textContent = parts.join(" — ") + ".";
  }

  function syncRadioPills() {
    [dom.colorModeLabels, dom.backgroundModeLabels].forEach(function (inputs) {
      inputs.forEach(function (input) {
        var pill = input.closest ? input.closest(".radio-pill") : null;
        if (pill) pill.classList.toggle("is-active", input.checked);
      });
    });
  }

  function syncInspector(line) {
    var edit = ensureEdit(line);
    var style = styleOf(line);
    var originalColor = normalizeHex(style.color) || "#22241f";
    var customColor = normalizeHex(edit.color) || originalColor;
    var confidence = Math.round(Number(line.confidence) || 0);

    dom.editEmpty.hidden = true;
    dom.editForm.hidden = false;
    dom.editLineId.textContent = line.id;
    dom.editConf.textContent = line.isNew ? "New box" : confidence + "% confidence";
    dom.editConf.className = "conf-pill " + (line.isNew ? "" : confidenceLevel(confidence));
    dom.editText.value = edit.text;
    dom.editOriginal.textContent = line.isNew
      ? "New text box — no source text"
      : line.text || "(empty line)";
    dom.editRemovedHint.hidden = line.isNew || !!edit.text.trim();
    dom.originalSwatch.style.background = originalColor;
    dom.originalSwatch.title = originalColor;
    dom.customSwatch.style.background = customColor;
    dom.customSwatch.title = customColor;

    dom.colorModes.forEach(function (input) {
      input.checked = input.value === edit.colorMode;
    });
    dom.customColorRow.hidden = edit.colorMode !== "custom";
    dom.colorPicker.value = customColor;
    dom.colorHex.value = customColor;

    var originalBackground = normalizeHex(style.background) || "#f5f2e8";
    var customBackground = effectiveBackground(edit);
    var backgroundBase = normalizeHex(edit.background) || originalBackground;
    var backgroundTone = Number(edit.backgroundTone);
    if (!isFinite(backgroundTone)) backgroundTone = 50;
    dom.originalBgSwatch.style.background = originalBackground;
    dom.originalBgSwatch.title = originalBackground;
    dom.customBgSwatch.style.background = customBackground;
    dom.customBgSwatch.title = customBackground;
    dom.backgroundModes.forEach(function (input) {
      input.checked = input.value === edit.backgroundMode;
    });
    dom.customBgRow.hidden = edit.backgroundMode !== "custom";
    dom.bgColorPicker.value = backgroundBase;
    dom.bgColorHex.value = backgroundBase;
    dom.bgToneRange.value = String(Math.round(backgroundTone));
    dom.bgToneOutput.textContent = Math.round(backgroundTone) + "%";

    if (edit.fontFamily) ensureFontFamilyOption(edit.fontFamily);
    dom.fontSelect.value = edit.fontFamily ? "family:" + edit.fontFamily : edit.font;
    dom.weightSelect.value = edit.weight;
    dom.underlineSelect.value = edit.underline;
    dom.strikeSelect.value = edit.strikethrough;
    dom.shadowSelect.value = edit.shadow;
    dom.scaleRange.value = String(Math.round(edit.scale * 100));
    dom.scaleOutput.textContent = Math.round(edit.scale * 100) + "%";
    dom.alignModes.forEach(function (input) {
      input.checked = input.value === edit.align;
    });
    dom.restoreLine.disabled = !isDirty(edit, line);
    if (dom.removeLineLabel) {
      dom.removeLineLabel.textContent = line.isNew ? "Delete box" : "Remove text";
    }
    updateFontHint(edit, line);
    syncRadioPills();
    syncCopyButtons();
    syncStyleSources(line);
  }

  function updateStageInfo() {
    if (!state.doc) {
      dom.stageInfo.textContent = "Hover a box to inspect it";
      return;
    }
    var line = state.hoverId ? state.lineIndex.get(state.hoverId) : selectedLine();
    if (line) {
      var style = styleOf(line);
      var bits = line.isNew
        ? [line.id, "new text box"]
        : [line.id, Math.round(Number(line.confidence) || 0) + "%"];
      if (style.font || style.font_name) {
        bits.push(faceSummary(style));
      }
      if (Number(style.font_size) > 0) {
        bits.push(Math.round(Number(style.font_size)) + " px");
      }
      dom.stageInfo.textContent = bits.join(" · ");
    } else if (state.addingText) {
      dom.stageInfo.textContent = "Click the image to place a new text box";
    } else if (!state.lines.length) {
      dom.stageInfo.textContent = "No text lines were detected";
    } else {
      dom.stageInfo.textContent = "Click any outlined text to edit it";
    }
  }

  function revealLineInStage(line) {
    if (!state.doc || !line || !line.box) return;
    var overlayRect = dom.overlay.getBoundingClientRect();
    var viewRect = dom.canvasScroll.getBoundingClientRect();
    if (!overlayRect.width || !overlayRect.height || !viewRect.width) return;

    var sx = overlayRect.width / state.doc.width;
    var sy = overlayRect.height / state.doc.height;
    var centerX = overlayRect.left + (line.box.x + line.box.width / 2) * sx;
    var centerY = overlayRect.top + (line.box.y + line.box.height / 2) * sy;
    var padding = 32;
    var dx = 0;
    var dy = 0;

    if (centerX < viewRect.left + padding) dx = centerX - (viewRect.left + padding);
    else if (centerX > viewRect.right - padding) dx = centerX - (viewRect.right - padding);
    if (centerY < viewRect.top + padding) dy = centerY - (viewRect.top + padding);
    else if (centerY > viewRect.bottom - padding) dy = centerY - (viewRect.bottom - padding);

    if (dx || dy) {
      try {
        dom.canvasScroll.scrollBy({
          left: dx,
          top: dy,
          behavior: prefersReducedMotion() ? "auto" : "smooth"
        });
      } catch (error) {
        dom.canvasScroll.scrollLeft += dx;
        dom.canvasScroll.scrollTop += dy;
      }
    }
  }

  function selectLine(id, options) {
    options = options || {};
    var line = state.lineIndex.get(id);
    if (!line) return;

    state.selectedId = id;
    ensureEdit(line);
    syncInspector(line);
    refreshLineSelection();
    setInspectorTab("edit");
    drawOverlay();
    updateStageInfo();

    if (options.scroll !== false) scrollLineIntoView(id);
    if (options.revealStage) revealLineInStage(line);
    if (options.openMobile && isMobileLayout()) openPanel("inspector");
  }

  function deselectLine() {
    state.selectedId = null;
    dom.editForm.hidden = true;
    dom.editEmpty.hidden = false;
    dom.editText.value = "";
    dom.editOriginal.textContent = "";
    dom.editRemovedHint.hidden = true;
    refreshLineSelection();
    drawOverlay();
    updateStageInfo();
    syncCopyButtons();
    if (isMobileLayout() && state.mobilePanel === "inspector") closeDrawers();
  }

  /* ----------------------------------------------------------- inspector UI */

  function setColorMode(mode) {
    var line = selectedLine();
    if (!line) return;
    var edit = ensureEdit(line);
    edit.colorMode = mode === "custom" ? "custom" : "original";
    dom.colorModes.forEach(function (input) {
      input.checked = input.value === edit.colorMode;
    });
    dom.customColorRow.hidden = edit.colorMode !== "custom";
    syncRadioPills();
  }

  function syncBackgroundSwatch(edit) {
    var custom = effectiveBackground(edit);
    var tone = Number(edit.backgroundTone);
    if (!isFinite(tone)) tone = 50;
    dom.customBgSwatch.style.background = custom;
    dom.customBgSwatch.title = custom;
    dom.bgToneOutput.textContent = Math.round(tone) + "%";
  }

  function setBackgroundMode(mode) {
    var line = selectedLine();
    if (!line) return;
    var edit = ensureEdit(line);
    edit.backgroundMode = mode === "custom" ? "custom" : "original";
    dom.backgroundModes.forEach(function (input) {
      input.checked = input.value === edit.backgroundMode;
    });
    dom.customBgRow.hidden = edit.backgroundMode !== "custom";
    syncBackgroundSwatch(edit);
    syncRadioPills();
  }

  function afterEditChange(line, delay) {
    var edit = ensureEdit(line);
    refreshLineItem(line);
    dom.editRemovedHint.hidden = line.isNew || !!edit.text.trim();
    dom.restoreLine.disabled = !isDirty(edit, line);
    updateFontHint(edit, line);
    drawOverlay();
    updateStageInfo();
    syncCopyButtons();
    if (state.pendingRetry) {
      state.pendingRetry.dismissed = false;
      updateRetryNotice();
    }
    schedulePreview(delay);
  }

  function applyCustomColor(line, value) {
    var hex = normalizeHex(value);
    if (!hex) return false;
    var edit = ensureEdit(line);
    edit.color = hex;
    edit.colorMode = "custom";
    dom.colorPicker.value = hex;
    dom.colorHex.value = hex;
    dom.customSwatch.style.background = hex;
    dom.customSwatch.title = hex;
    dom.colorModes.forEach(function (input) {
      input.checked = input.value === "custom";
    });
    dom.customColorRow.hidden = false;
    syncRadioPills();
    afterEditChange(line, 380);
    return true;
  }

  function applyBackgroundColor(line, value) {
    var hex = normalizeHex(value);
    if (!hex) return false;
    var edit = ensureEdit(line);
    edit.background = hex;
    edit.backgroundMode = "custom";
    dom.bgColorPicker.value = hex;
    dom.bgColorHex.value = hex;
    dom.backgroundModes.forEach(function (input) {
      input.checked = input.value === "custom";
    });
    dom.customBgRow.hidden = false;
    syncBackgroundSwatch(edit);
    syncRadioPills();
    afterEditChange(line, 380);
    return true;
  }

  function setInspectorTab(tab, options) {
    options = options || {};
    var name = tab === "document" ? "document" : "edit";
    dom.inspectorTabs.forEach(function (button) {
      var active = button.getAttribute("data-tab") === name;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
    dom.editTab.hidden = name !== "edit";
    dom.documentTab.hidden = name !== "document";
    if (options.openMobile && isMobileLayout()) openPanel("inspector");
  }

  /* --------------------------------------------------------------- drawers */

  function openPanel(name) {
    if (!isMobileLayout() || (name !== "lines" && name !== "inspector")) return;
    state.mobilePanel = name;
    dom.linesPanel.classList.toggle("is-open", name === "lines");
    dom.inspector.classList.toggle("is-open", name === "inspector");
    dom.sideBackdrop.hidden = false;
  }

  function closeDrawers() {
    state.mobilePanel = null;
    if (dom.linesPanel) dom.linesPanel.classList.remove("is-open");
    if (dom.inspector) dom.inspector.classList.remove("is-open");
    if (dom.sideBackdrop) dom.sideBackdrop.hidden = true;
  }

  function togglePanel(name) {
    if (state.mobilePanel === name) closeDrawers();
    else openPanel(name);
  }

  /* ------------------------------------------------------------ stage hints */

  function showStageHint(text, timeout) {
    if (!dom.stageFloatHint) return;
    if (state.stageHintTimer) window.clearTimeout(state.stageHintTimer);
    state.stageHintTimer = null;
    dom.stageFloatText.textContent = text;
    dom.stageFloatHint.hidden = false;
    if (timeout) {
      state.stageHintTimer = window.setTimeout(hideStageHint, timeout);
    }
  }

  function hideStageHint() {
    if (state.stageHintTimer) {
      window.clearTimeout(state.stageHintTimer);
      state.stageHintTimer = null;
    }
    if (dom.stageFloatHint) dom.stageFloatHint.hidden = true;
  }

  /* -------------------------------------------------------------- overlay */

  function sizeOverlay() {
    if (!state.doc) return;
    var width = dom.imageWrap.clientWidth || Math.round(state.doc.width * state.scale);
    var height = dom.imageWrap.clientHeight || Math.round(state.doc.height * state.scale);
    var ratio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    var pixelWidth = Math.max(1, Math.round(width * ratio));
    var pixelHeight = Math.max(1, Math.round(height * ratio));
    if (dom.overlay.width !== pixelWidth) dom.overlay.width = pixelWidth;
    if (dom.overlay.height !== pixelHeight) dom.overlay.height = pixelHeight;
    drawOverlay();
  }

  function drawOverlay() {
    var canvas = dom.overlay;
    if (!canvas) return;
    var context = canvas.getContext("2d");
    if (!context) return;

    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (!state.doc || !state.lines.length || canvas.width < 2 || canvas.height < 2) return;

    var docWidth = Number(state.doc.width) || canvas.width;
    var docHeight = Number(state.doc.height) || canvas.height;
    var scaleX = canvas.width / docWidth;
    var scaleY = canvas.height / docHeight;
    // Canvas backing pixels include devicePixelRatio. Line widths and label
    // sizes should stay constant in CSS pixels, so divide the backing scale
    // back down before using it for stroke/font sizes.
    var cssWidth = dom.imageWrap.clientWidth || canvas.width;
    var pixelRatio = Math.max(1, canvas.width / Math.max(1, cssWidth));
    var strokeScale = Math.max(0.0001, Math.min(scaleX, scaleY) / pixelRatio);

    context.setTransform(scaleX, 0, 0, scaleY, 0, 0);

    state.lines.forEach(function (line) {
      if (!line.box || !lineIsVisible(line)) return;
      var box = line.box;
      var width = Math.max(1, Number(box.width) || 0);
      var height = Math.max(1, Number(box.height) || 0);
      var selected = state.selectedId === line.id;
      var hovered = state.hoverId === line.id;
      var edit = state.edits.get(line.id);
      var dirty = !!(edit && isDirty(edit, line));
      var removed = dirty && !normalizeText(edit.text).trim();

      context.save();

      if (selected) {
        context.fillStyle = "rgba(91, 124, 255, 0.22)";
        context.fillRect(box.x, box.y, width, height);
        context.strokeStyle = "#5b7cff";
        context.lineWidth = 2.2 / strokeScale;
      } else if (hovered) {
        context.fillStyle = "rgba(91, 124, 255, 0.14)";
        context.fillRect(box.x, box.y, width, height);
        context.strokeStyle = "rgba(91, 124, 255, 0.95)";
        context.lineWidth = 1.7 / strokeScale;
      } else if (dirty) {
        context.strokeStyle = "rgba(63, 116, 80, 0.95)";
        context.lineWidth = 1.35 / strokeScale;
      } else {
        context.strokeStyle = "rgba(91, 124, 255, 0.88)";
        context.lineWidth = 1.1 / strokeScale;
      }

      if (removed) {
        context.setLineDash([5 / strokeScale, 3.5 / strokeScale]);
      }
      context.strokeRect(
        Math.round(box.x) + 0.5 / strokeScale,
        Math.round(box.y) + 0.5 / strokeScale,
        Math.max(1, Math.round(width) - 1 / strokeScale),
        Math.max(1, Math.round(height) - 1 / strokeScale)
      );

      if (selected || hovered) {
        var label = line.id + (line.isNew ? " · new" : dirty ? " · edited" : "");
        var fontSize = 10.5 / strokeScale;
        context.setLineDash([]);
        context.font = "600 " + fontSize + "px ui-monospace, Menlo, Consolas, monospace";
        var padding = 4 / strokeScale;
        var textWidth = context.measureText(label).width;
        var labelHeight = fontSize + padding * 2;
        var labelX = box.x;
        var labelY = box.y - labelHeight - 2 / strokeScale;
        if (labelY < 1 / strokeScale) labelY = box.y + height + 2 / strokeScale;
        context.fillStyle = selected ? "#2448d8" : "rgba(11, 15, 18, 0.92)";
        context.fillRect(labelX, labelY, textWidth + padding * 2, labelHeight);
        context.fillStyle = "#ffffff";
        context.textBaseline = "middle";
        context.fillText(label, labelX + padding, labelY + labelHeight / 2 + fontSize * 0.04);
      }

      context.restore();
    });
  }

  function stagePoint(clientX, clientY) {
    if (!state.doc) return null;
    var rect = dom.overlay.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return {
      x: clamp((clientX - rect.left) * (state.doc.width / rect.width), 0, state.doc.width),
      y: clamp((clientY - rect.top) * (state.doc.height / rect.height), 0, state.doc.height)
    };
  }

  function hitTestLine(clientX, clientY) {
    if (!state.doc || !state.lines.length) return null;
    var rect = dom.overlay.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    var x = (clientX - rect.left) * (state.doc.width / rect.width);
    var y = (clientY - rect.top) * (state.doc.height / rect.height);
    var tolerance = 4 * (state.doc.width / rect.width);
    var best = null;
    var bestArea = Infinity;

    state.lines.forEach(function (line) {
      if (!line.box || !lineIsVisible(line)) return;
      var box = line.box;
      if (x < box.x - tolerance || y < box.y - tolerance ||
          x > box.x + box.width + tolerance || y > box.y + box.height + tolerance) {
        return;
      }
      var area = Math.max(1, Number(box.width) * Number(box.height));
      if (area < bestArea) {
        bestArea = area;
        best = line;
      }
    });
    return best;
  }

  function handleOverlayMove(event) {
    if (event.pointerType === "touch") return;
    if (state.addingText) {
      if (state.hoverId) {
        state.hoverId = null;
        drawOverlay();
      }
      dom.overlay.style.cursor = "text";
      return;
    }
    var line = hitTestLine(event.clientX, event.clientY);
    var next = line ? line.id : null;
    if (state.hoverId !== next) {
      state.hoverId = next;
      drawOverlay();
    }
    dom.overlay.style.cursor = next ? "pointer" : "crosshair";
    updateStageInfo();
  }

  function handleOverlayLeave() {
    if (state.hoverId) {
      state.hoverId = null;
      drawOverlay();
    }
    dom.overlay.style.cursor = state.addingText ? "text" : "crosshair";
    updateStageInfo();
  }

  /* ----------------------------------------------------------------- zoom */

  function calculateFitScale() {
    if (!state.doc) return 1;
    var viewWidth = dom.canvasScroll.clientWidth;
    var viewHeight = dom.canvasScroll.clientHeight;
    if (!viewWidth || !viewHeight) return 1;
    var availableWidth = Math.max(120, viewWidth - 56);
    var availableHeight = Math.max(120, viewHeight - 56);
    var scale = Math.min(
      availableWidth / Math.max(1, state.doc.width),
      availableHeight / Math.max(1, state.doc.height)
    );
    return clamp(scale, 0.05, 1.5);
  }

  function updateZoomControls() {
    if (!state.doc) return;
    var percent = Math.round(state.scale * 100);
    dom.zoomValue.textContent = state.zoomMode === "fit" ? "Fit" : percent + "%";
    dom.zoomReadout.textContent = state.zoomMode === "fit" ? "Fit · " + percent + "%" : percent + "%";
    dom.zoomOut.disabled = state.scale <= 0.101;
    dom.zoomIn.disabled = state.scale >= 3.999;
  }

  function applyZoom(mode) {
    if (!state.doc) return;
    var wasFit = state.zoomMode === "fit";
    var fit = calculateFitScale();
    var relativeX = 0.5;
    var relativeY = 0.5;

    if (!wasFit || mode !== "fit") {
      var scrollRect = dom.canvasScroll.getBoundingClientRect();
      var wrapRect = dom.imageWrap.getBoundingClientRect();
      if (wrapRect.width && wrapRect.height && scrollRect.width) {
        relativeX = (scrollRect.left + scrollRect.width / 2 - wrapRect.left) / wrapRect.width;
        relativeY = (scrollRect.top + scrollRect.height / 2 - wrapRect.top) / wrapRect.height;
      }
    }

    if (mode === "fit") {
      state.scale = fit;
      state.zoomMode = "fit";
    } else if (mode === "in") {
      state.scale = clamp((wasFit ? fit : state.scale) * 1.2, 0.1, 4);
      state.zoomMode = "manual";
    } else if (mode === "out") {
      state.scale = clamp((wasFit ? fit : state.scale) / 1.2, 0.1, 4);
      state.zoomMode = "manual";
    } else if (typeof mode === "number" && isFinite(mode)) {
      state.scale = clamp(mode, 0.1, 4);
      state.zoomMode = "manual";
    }

    state.needFit = false;
    dom.imageWrap.style.width = Math.max(1, Math.round(state.doc.width * state.scale)) + "px";
    dom.imageWrap.style.height = Math.max(1, Math.round(state.doc.height * state.scale)) + "px";
    updateZoomControls();
    sizeOverlay();

    if (mode !== "fit" && relativeX && relativeY) {
      var scrollBox = dom.canvasScroll.getBoundingClientRect();
      window.requestAnimationFrame(function () {
        var next = dom.imageWrap.getBoundingClientRect();
        var centerX = scrollBox.left + scrollBox.width / 2;
        var centerY = scrollBox.top + scrollBox.height / 2;
        dom.canvasScroll.scrollLeft += next.left + relativeX * next.width - centerX;
        dom.canvasScroll.scrollTop += next.top + relativeY * next.height - centerY;
      });
    }
  }

  function handleStageImageLoad() {
    if (!state.doc || state.view !== "editor") return;
    dom.stageLoader.hidden = true;
    sizeOverlay();
    updateStageInfo();
  }

  function handleStageImageError() {
    dom.stageLoader.hidden = true;
    showToast("The image could not be displayed. It may have expired.", "error", 8000);
  }

  /* --------------------------------------------------------------- download */

  function exportName(name) {
    var stem = String(name || "image").replace(/\.[^.]+$/, "");
    stem = stem.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
    return (stem || "image") + "-edited.png";
  }

  async function downloadImage() {
    if (!state.doc || state.downloading || state.busy) return false;
    var docId = state.doc.id;
    state.downloading = true;
    dom.downloadBtn.disabled = true;
    dom.downloadBtn.setAttribute("aria-busy", "true");

    try {
      if (currentSignature() !== state.renderedSignature) {
        var rendered = await renderCurrent({ force: true });
        if (!rendered || !state.doc || state.doc.id !== docId) return false;
      }

      var delivered = state.renderedSignature;
      var response;
      try {
        response = await fetch("/api/documents/" + encodeURIComponent(docId) + "/download", {
          credentials: "same-origin"
        });
      } catch (error) {
        throw new ApiError("Cannot reach the InkPatch server. Is it running?", 0, null);
      }

      if (!response.ok) {
        var data = null;
        try { data = await response.json(); } catch (error) { data = null; }
        throw new ApiError(
          extractError(data) || "The PNG could not be downloaded (" + response.status + ").",
          response.status,
          data
        );
      }

      var blob = await response.blob();
      if (!state.doc || state.doc.id !== docId) return false;
      var filename = exportName(state.doc.name);
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.rel = "noopener";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.setTimeout(function () {
        try { URL.revokeObjectURL(url); } catch (error) { /* ignore */ }
      }, 1600);

      state.downloadedSignature = delivered;
      resolveRetry("download");
      showToast("Saved " + filename + ".", "success");
      return true;
    } catch (error) {
      handleError(error, "download");
      return false;
    } finally {
      state.downloading = false;
      if (dom.downloadBtn) {
        dom.downloadBtn.disabled = false;
        dom.downloadBtn.removeAttribute("aria-busy");
      }
    }
  }

  /* ----------------------------------------------------------------- rescan */

  async function rescanDocument() {
    return rescanDocumentWithOptions();
  }

  async function rescanDocumentWithOptions(options) {
    options = options || {};
    if (!state.doc || state.busy) return false;
    var docId = state.doc.id;
    var lang = dom.docLang.value || state.doc.lang || "eng";
    var psm = Number(dom.docPsm.value) || 3;

    if (!options.confirmed && currentSignature() !== "") {
      var ok = await confirmAction({
        title: "Re-read this image?",
        text: "OCR will run again from the source pixels. Every edit made in this session will be discarded.",
        okLabel: "Re-read image",
        cancelLabel: "Keep my edits",
        danger: false
      });
      if (!ok) return false;
    }

    state.busy = true;
    dom.rescanBtn.disabled = true;
    showBusy({
      eyebrow: "Document settings",
      title: "Re-reading the image",
      text: "Tesseract is detecting lines and sampling their type styles again."
    });

    try {
      var doc = await apiJSON(
        "/api/documents/" + encodeURIComponent(docId) + "/ocr",
        { method: "POST", body: JSON.stringify({ lang: lang, psm: psm }) }
      );
      if (!state.doc || state.doc.id !== docId) return;
      openDocument(doc);
      if (doc.warning) {
        showToast(doc.warning, "warning", 9000);
      } else {
        showToast("Re-read complete. " + plural(doc.line_count || 0, "text line") + " found.", "success");
      }
      return true;
    } catch (error) {
      handleError(error, "rescan");
      return false;
    } finally {
      state.busy = false;
      dom.rescanBtn.disabled = false;
      hideBusy();
      updateUploadEnabled();
    }
  }

  /* --------------------------------------------------------------- keyboard */

  function trapModalFocus(event) {
    var items = $$("button:not(:disabled)", dom.modalRoot).filter(function (button) {
      return button.offsetParent !== null;
    });
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    } else if (items.indexOf(document.activeElement) === -1) {
      event.preventDefault();
      first.focus();
    }
  }

  function handleKeydown(event) {
    if (!dom.modalRoot.hidden) {
      if (event.key === "Escape") {
        event.preventDefault();
        resolveModal(false);
      } else if (event.key === "Enter") {
        event.preventDefault();
        resolveModal(document.activeElement !== dom.modalCancel);
      } else if (event.key === "Tab") {
        trapModalFocus(event);
      }
      return;
    }

    if (state.view !== "editor" || !dom.busyOverlay.hidden) return;
    var target = event.target || {};
    var tag = String(target.tagName || "").toLowerCase();
    var typing = tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable;

    if (event.key === "Escape") {
      if (typing) {
        if (target.blur) target.blur();
        return;
      }
      if (state.addingText) {
        event.preventDefault();
        setAddTextMode(false);
        return;
      }
      if (state.mobilePanel) {
        event.preventDefault();
        closeDrawers();
        return;
      }
      if (state.selectedId) {
        event.preventDefault();
        deselectLine();
      }
      return;
    }

    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      renderCurrent({ force: true });
      return;
    }

    if ((event.ctrlKey || event.metaKey) && String(event.key || "").toLowerCase() === "s") {
      event.preventDefault();
      downloadImage();
      return;
    }

    if (event.key === "/" && !typing) {
      event.preventDefault();
      if (isMobileLayout()) openPanel("lines");
      dom.lineSearch.focus();
    }
  }

  /* ----------------------------------------------------------------- events */

  function listen(target, event, handler, options) {
    if (target) target.addEventListener(event, handler, options);
  }

  function handleResize() {
    if (state.resizeTimer) window.clearTimeout(state.resizeTimer);
    state.resizeTimer = window.setTimeout(function () {
      state.resizeTimer = null;
      if (state.view !== "editor" || !state.doc) return;
      if (!isMobileLayout()) closeDrawers();
      if (state.zoomMode === "fit") applyZoom("fit");
      else sizeOverlay();
    }, 140);
  }

  function bindEvents() {
    listen(dom.brandHome, "click", function (event) {
      event.preventDefault();
      if (state.view === "editor") leaveEditor();
      else window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
    });
    listen(dom.themeToggle, "click", toggleTheme);
    listen(dom.retryAllBtn, "click", retryAllPending);
    listen(dom.retryNoticeClose, "click", function () {
      if (!state.pendingRetry) return;
      state.pendingRetry.dismissed = true;
      updateRetryNotice();
    });

    listen(dom.backBtn, "click", function () { leaveEditor(); });
    listen(dom.newImageBtn, "click", function () { leaveEditor(); });
    listen(dom.startOverBtn, "click", function () {
      leaveEditor({
        title: "Start over?",
        text: "This closes the current image. Any edits that have not been downloaded will be discarded.",
        okLabel: "Start over"
      });
    });
    listen(dom.downloadBtn, "click", downloadImage);

    listen(dom.retryEngine, "click", async function () {
      var ok = await refreshHealth(true);
      if (ok) showToast("Tesseract is ready. You can read an image now.", "success");
      else showToast("Tesseract was not found yet. Follow the install commands, then check again.", "error", 8000);
    });

    listen(dom.uploadForm, "submit", function (event) {
      event.preventDefault();
      startUpload();
    });

    listen(dom.dropzone, "click", function () {
      if (!state.busy) dom.fileInput.click();
    });
    listen(dom.dropzone, "keydown", function (event) {
      if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
        event.preventDefault();
        if (!state.busy) dom.fileInput.click();
      }
    });

    var dragDepth = 0;
    listen(dom.dropzone, "dragenter", function (event) {
      event.preventDefault();
      dragDepth += 1;
      dom.dropzone.classList.add("is-dragging");
    });
    listen(dom.dropzone, "dragover", function (event) {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    });
    listen(dom.dropzone, "dragleave", function (event) {
      event.preventDefault();
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) dom.dropzone.classList.remove("is-dragging");
    });
    listen(dom.dropzone, "drop", function (event) {
      event.preventDefault();
      dragDepth = 0;
      dom.dropzone.classList.remove("is-dragging");
      var file = event.dataTransfer && event.dataTransfer.files ? event.dataTransfer.files[0] : null;
      if (file) selectFile(file);
    });
    listen(window, "dragover", function (event) {
      if (state.view === "landing" && event.target === document.body) event.preventDefault();
    });
    listen(window, "drop", function (event) {
      if (state.view === "landing" && event.target === document.body) event.preventDefault();
      dragDepth = 0;
      dom.dropzone.classList.remove("is-dragging");
    });

    listen(dom.fileInput, "change", function () {
      if (this.files && this.files[0]) selectFile(this.files[0]);
    });
    listen(dom.clearFile, "click", clearFile);
    listen(dom.demoBtn, "click", startDemo);

    listen(dom.lineSearch, "input", function () {
      state.search = this.value;
      applyFilters();
    });
    listen(dom.lineSearch, "keydown", function (event) {
      if (event.key === "Escape") {
        event.stopPropagation();
        state.search = "";
        this.value = "";
        applyFilters();
        this.blur();
        if (isMobileLayout() && state.mobilePanel === "lines") closeDrawers();
      } else if (event.key === "Enter") {
        var first = visibleLineButtons()[0];
        if (first) {
          event.preventDefault();
          first.click();
        }
      }
    });
    listen(dom.searchClear, "click", function () {
      state.search = "";
      dom.lineSearch.value = "";
      applyFilters();
      dom.lineSearch.focus();
    });
    dom.filterTabs.forEach(function (tab) {
      listen(tab, "click", function () {
        setFilter(tab.getAttribute("data-filter"));
      });
    });
    listen(dom.copyAllBtn, "click", copyAllImageText);
    listen(dom.dotAllBtn, "click", appendDotToAllLines);

    listen(dom.closeLines, "click", closeDrawers);
    listen(dom.closeInspector, "click", closeDrawers);
    listen(dom.sideBackdrop, "click", closeDrawers);
    listen(dom.mobileLinesBtn, "click", function () { togglePanel("lines"); });
    listen(dom.docSettingsBtn, "click", function () {
      setInspectorTab("document", { openMobile: true });
    });
    dom.inspectorTabs.forEach(function (button) {
      listen(button, "click", function () {
        setInspectorTab(button.getAttribute("data-tab"));
      });
    });

    listen(dom.zoomIn, "click", function () { applyZoom("in"); });
    listen(dom.zoomOut, "click", function () { applyZoom("out"); });
    listen(dom.zoomFit, "click", function () { applyZoom("fit"); });
    listen(dom.zoomValue, "click", function () { applyZoom("fit"); });
    listen(dom.addTextBtn, "click", function () {
      if (!state.doc) return;
      setAddTextMode(!state.addingText);
    });
    listen(dom.styleSourceSelect, "change", function () {
      if (dom.copyStyleBtn) dom.copyStyleBtn.disabled = !this.value;
    });
    listen(dom.copyStyleBtn, "click", copyStyleFromSource);
    listen(dom.overlay, "pointermove", handleOverlayMove);
    listen(dom.overlay, "pointerleave", handleOverlayLeave);
    listen(dom.overlay, "click", function (event) {
      if (state.addingText) {
        event.preventDefault();
        var point = stagePoint(event.clientX, event.clientY);
        if (point) addTextAt(point.x, point.y);
        return;
      }
      var line = hitTestLine(event.clientX, event.clientY);
      if (line) selectLine(line.id, { openMobile: true, scroll: true });
      else deselectLine();
    });
    listen(dom.docImage, "load", handleStageImageLoad);
    listen(dom.docImage, "error", handleStageImageError);

    listen(dom.livePreview, "change", function () {
      state.livePreview = this.checked;
      savePreference("inkpatch.livePreview", state.livePreview ? "1" : "0");
      if (!state.livePreview) cancelPreviewTimer();
      updatePreviewUI();
      if (state.livePreview) schedulePreview(280);
    });

    listen(dom.editText, "input", function () {
      var line = selectedLine();
      if (!line) return;
      var edit = ensureEdit(line);
      edit.text = this.value;
      afterEditChange(line, 650);
    });

    dom.colorModes.forEach(function (input) {
      listen(input, "change", function () {
        if (!this.checked) return;
        var line = selectedLine();
        if (!line) return;
        setColorMode(this.value);
        afterEditChange(line, 420);
      });
    });

    listen(dom.colorPicker, "input", function () {
      var line = selectedLine();
      if (!line) return;
      applyCustomColor(line, this.value);
    });
    listen(dom.colorHex, "input", function () {
      var line = selectedLine();
      if (!line) return;
      var hex = normalizeHex(this.value);
      if (hex) applyCustomColor(line, hex);
    });
    listen(dom.colorHex, "blur", function () {
      var line = selectedLine();
      if (!line) return;
      var edit = ensureEdit(line);
      if (!normalizeHex(this.value)) {
        this.value = normalizeHex(edit.color) || "#22241f";
      }
    });
    listen(dom.colorHex, "keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        this.blur();
      }
    });

    dom.backgroundModes.forEach(function (input) {
      listen(input, "change", function () {
        if (!this.checked) return;
        var line = selectedLine();
        if (!line) return;
        setBackgroundMode(this.value);
        afterEditChange(line, 420);
      });
    });

    listen(dom.bgColorPicker, "input", function () {
      var line = selectedLine();
      if (!line) return;
      applyBackgroundColor(line, this.value);
    });
    listen(dom.bgColorHex, "input", function () {
      var line = selectedLine();
      if (!line) return;
      var hex = normalizeHex(this.value);
      if (hex) applyBackgroundColor(line, hex);
    });
    listen(dom.bgColorHex, "blur", function () {
      var line = selectedLine();
      if (!line) return;
      var edit = ensureEdit(line);
      if (!normalizeHex(this.value)) {
        this.value = normalizeHex(edit.background) || "#f5f2e8";
      }
    });
    listen(dom.bgColorHex, "keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        this.blur();
      }
    });
    listen(dom.bgToneRange, "input", function () {
      var line = selectedLine();
      if (!line) return;
      var edit = ensureEdit(line);
      edit.backgroundTone = Number(this.value);
      syncBackgroundSwatch(edit);
      afterEditChange(line, 320);
    });

    listen(dom.fontSelect, "change", function () {
      var line = selectedLine();
      if (!line) return;
      var edit = ensureEdit(line);
      if (this.value.indexOf("family:") === 0) {
        edit.fontFamily = this.value.slice(7);
        edit.font = "auto";
      } else {
        edit.font = this.value;
        edit.fontFamily = "";
      }
      afterEditChange(line, 450);
    });
    listen(dom.weightSelect, "change", function () {
      var line = selectedLine();
      if (!line) return;
      ensureEdit(line).weight = this.value;
      afterEditChange(line, 450);
    });
    listen(dom.underlineSelect, "change", function () {
      var line = selectedLine();
      if (!line) return;
      ensureEdit(line).underline = this.value;
      afterEditChange(line, 450);
    });
    listen(dom.strikeSelect, "change", function () {
      var line = selectedLine();
      if (!line) return;
      ensureEdit(line).strikethrough = this.value;
      afterEditChange(line, 450);
    });
    listen(dom.shadowSelect, "change", function () {
      var line = selectedLine();
      if (!line) return;
      ensureEdit(line).shadow = this.value;
      afterEditChange(line, 450);
    });
    dom.alignModes.forEach(function (input) {
      listen(input, "change", function () {
        if (!this.checked) return;
        var line = selectedLine();
        if (!line) return;
        ensureEdit(line).align = this.value;
        afterEditChange(line, 500);
      });
    });
    listen(dom.scaleRange, "input", function () {
      var line = selectedLine();
      if (!line) return;
      ensureEdit(line).scale = Number(this.value) / 100;
      dom.scaleOutput.textContent = this.value + "%";
      afterEditChange(line, 700);
    });

    listen(dom.copyLineBtn, "click", copySelectedLineText);

    listen(dom.restoreLine, "click", function () {
      var line = selectedLine();
      if (!line) return;
      state.edits.delete(line.id);
      ensureEdit(line);
      syncInspector(line);
      refreshLineItem(line);
      drawOverlay();
      updateStageInfo();
      updatePreviewUI();
      schedulePreview(180);
      showToast(
        line.isNew
          ? "New box style reset. Its text and position are unchanged."
          : "Line restored to its original text and style.",
        "info",
        2600
      );
    });

    listen(dom.removeLine, "click", function () {
      var line = selectedLine();
      if (!line) return;
      if (line.isNew) {
        removeNewLine(line);
        return;
      }
      ensureEdit(line).text = "";
      dom.editText.value = "";
      dom.editText.focus();
      afterEditChange(line, 320);
      showToast("The text will disappear when the preview renders.", "info", 2800);
    });

    listen(dom.previewBtn, "click", function () {
      renderCurrent({ force: true });
    });
    listen(dom.rescanBtn, "click", rescanDocument);

    listen(dom.modalCancel, "click", function () { resolveModal(false); });
    listen(dom.modalOk, "click", function () { resolveModal(true); });
    listen(dom.modalRoot, "click", function (event) {
      if (event.target && event.target.hasAttribute && event.target.hasAttribute("data-modal-close")) {
        resolveModal(false);
      }
    });

    listen(document, "keydown", handleKeydown);
    listen(window, "resize", handleResize);
    listen(window, "beforeunload", function (event) {
      if (!state.doc) return;
      var signature = currentSignature();
      if (signature && signature !== state.downloadedSignature) {
        event.preventDefault();
        event.returnValue = "";
      }
    });
  }

  /* ------------------------------------------------------------------- init */

  function init() {
    cacheDom();
    if (!dom.landingView) return;
    applyTheme(state.theme, false);
    dom.livePreview.checked = state.livePreview;
    bindEvents();
    refreshHealth();
    loadFontCatalog();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();