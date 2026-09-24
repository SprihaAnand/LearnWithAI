(() => {
  "use strict";

  const $ = (selector, parent = document) => parent.querySelector(selector);
  const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
  const state = {
    token: localStorage.getItem("learnwithai_token") || "",
    user: null,
    courses: [],
    course: null,
    lesson: null,
    quiz: null,
    playback: null,
    geminiConfigured: false,
    uploadLimits: null,
  };

  const escapeHtml = (value = "") => String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const initials = (name = "Learner") => name.split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
  const duration = (seconds = 0) => {
    const mins = Math.max(1, Math.round(Number(seconds || 0) / 60));
    return `${mins} min`;
  };

  const formatClock = (seconds = 0) => {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    const minutes = Math.floor(value / 60);
    const remaining = String(value % 60).padStart(2, "0");
    return `${String(minutes).padStart(2, "0")}:${remaining}`;
  };

  const hasText = (value) => typeof value === "string" && value.trim().length > 0;

  function toast(message, tone = "") {
    const node = document.createElement("div");
    node.className = `toast ${tone}`;
    node.textContent = message;
    $("#toastRegion")?.append(node);
    window.setTimeout(() => node.remove(), 4200);
  }

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    const multipart = typeof FormData !== "undefined" && options.body instanceof FormData;
    if (options.body && !multipart && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch(path, { ...options, headers });
    } catch {
      throw new Error(multipart
        ? "The video upload connection was interrupted. Your form is still here. Check your connection and whether the course was created before trying again."
        : "Could not connect to LearnWithAI. Check your connection and try again.");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data?.error?.message || "Something went wrong. Please try again.");
    return data;
  }

  function setView(name, { scroll = true } = {}) {
    $$(".view").forEach((view) => view.classList.toggle("is-hidden", view.dataset.view !== name));
    document.body.dataset.currentView = name;
    if (scroll) window.scrollTo({ top: 0, behavior: "smooth" });
    $$("[data-route]").forEach((element) => element.classList.toggle("active", element.dataset.route === name));
    $("#mainNav")?.classList.remove("is-open");
    $("#menuToggle")?.setAttribute("aria-expanded", "false");
    if (name === "admin" && state.user?.role === "admin") {
      refreshUploadLimits().catch(() => {
        const hint = $("#uploadLimitsHint");
        if (hint) hint.textContent = "Upload limits could not be loaded. We will check them again before publishing.";
      });
    }
  }

  function setAuthMode(mode = "login") {
    setView("auth");
    const register = mode === "register";
    $("#loginFormWrap")?.classList.toggle("is-hidden", register);
    $("#registerFormWrap")?.classList.toggle("is-hidden", !register);
    $("#loginTab")?.setAttribute("aria-selected", String(!register));
    $("#registerTab")?.setAttribute("aria-selected", String(register));
    window.setTimeout(() => $(register ? "#registerForm input" : "#loginForm input")?.focus(), 100);
  }

  function updateChrome() {
    const signedIn = Boolean(state.user);
    $$(".auth-entry").forEach((element) => element.classList.toggle("is-hidden", signedIn));
    $$(".member-link, .profile-button").forEach((element) => element.classList.toggle("is-hidden", !signedIn));
    $$(".admin-only").forEach((element) => element.classList.toggle("is-hidden", state.user?.role !== "admin"));
    if (signedIn) {
      $("#headerAvatar").textContent = initials(state.user.name);
      $("#headerName").textContent = state.user.name.split(" ")[0];
      $("#dashboardName").textContent = state.user.name.split(" ")[0];
    }
    renderGeminiStatus();
  }

  function renderGeminiStatus() {
    const configured = Boolean(state.user && state.geminiConfigured);
    const tutorStatus = $("#geminiTutorStatus");
    if (tutorStatus) {
      tutorStatus.textContent = configured ? "Gemini key saved for this session" : "No Gemini key saved";
      tutorStatus.classList.toggle("is-connected", configured);
    }
    const settingsStatus = $("#geminiKeyStatus");
    if (settingsStatus) settingsStatus.textContent = configured
      ? "Gemini key saved for this session. Your next question will check access to Gemini. You can remove the key at any time."
      : "No Gemini key is saved for this session.";
    const removeButton = $("#removeGeminiKey");
    if (removeButton) removeButton.disabled = !configured;
    if ($("#lessonVideoFile")) syncMediaSourceControls();
  }

  async function refreshGeminiStatus() {
    if (!state.user || !state.token) {
      state.geminiConfigured = false;
      renderGeminiStatus();
      return false;
    }
    try {
      const data = await api("/api/session/gemini-key");
      state.geminiConfigured = Boolean(data.configured ?? data.has_key ?? data.gemini_configured ?? data.status === "configured");
    } catch {
      state.geminiConfigured = false;
    }
    renderGeminiStatus();
    return state.geminiConfigured;
  }

  function syncOverlay() {
    const hasOpenPanel = $("#tutorPanel")?.classList.contains("is-open") || $("#geminiPanel")?.classList.contains("is-open");
    $("#overlay")?.classList.toggle("is-visible", Boolean(hasOpenPanel));
  }

  async function openGeminiSettings() {
    if (!state.user) return setAuthMode("login");
    closeTutor();
    $("#profileMenu")?.classList.add("is-hidden");
    $("#geminiPanel")?.classList.add("is-open");
    $("#geminiPanel")?.setAttribute("aria-hidden", "false");
    syncOverlay();
    await refreshGeminiStatus();
    window.setTimeout(() => $("#geminiApiKey")?.focus(), 120);
  }

  function closeGeminiSettings() {
    $("#geminiPanel")?.classList.remove("is-open");
    $("#geminiPanel")?.setAttribute("aria-hidden", "true");
    const input = $("#geminiApiKey");
    if (input) input.value = "";
    syncOverlay();
  }

  async function saveGeminiKey(event) {
    event.preventDefault();
    if (!state.user) return setAuthMode("login");
    const input = $("#geminiApiKey");
    const apiKey = input?.value.trim();
    if (!apiKey) return toast("Paste a Gemini API key to continue.", "error");
    const submit = $("#geminiKeyForm button[type='submit']");
    try {
      submit.disabled = true;
      $("#geminiKeyStatus").textContent = "Saving your Gemini key for this session…";
      await api("/api/session/gemini-key", { method: "PUT", body: JSON.stringify({ api_key: apiKey }) });
      input.value = "";
      state.geminiConfigured = true;
      renderGeminiStatus();
      toast("Gemini key saved. Ask a lesson question to check access.", "success");
    } catch (error) {
      $("#geminiKeyStatus").textContent = error.message;
      toast(error.message, "error");
    } finally {
      submit.disabled = false;
    }
  }

  async function removeGeminiKey() {
    if (!state.user || !state.geminiConfigured) return;
    try {
      await api("/api/session/gemini-key", { method: "DELETE" });
      state.geminiConfigured = false;
      renderGeminiStatus();
      toast("Your Gemini key was removed from this session.");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function cardMarkup(course) {
    const progress = Math.round(course.progress_percent || 0);
    const tag = escapeHtml(course.category || "Community learning");
    const title = escapeHtml(course.title);
    const summary = escapeHtml(course.summary || "Practical learning for everyday life.");
    return `<article class="course-card">
      <button class="course-visual topic-${String(course.category || "general").toLowerCase().replace(/[^a-z]+/g, "-")}" type="button" data-open-course="${course.id}" aria-label="Open ${title}">
        <span class="course-visual-pattern" aria-hidden="true"></span><span class="course-visual-icon" aria-hidden="true">✦</span>
        <span class="course-badge">${tag}</span><span class="course-duration">${course.lesson_count || 0} lessons</span><span class="course-play" aria-hidden="true">▶</span>
      </button>
      <div class="course-card-body"><p class="course-meta">${course.enrolled ? "Your learning path" : "Open course"}</p>
        <h3><button type="button" class="course-title-button" data-open-course="${course.id}">${title}</button></h3><p class="course-description">${summary}</p>
        <div class="course-card-footer"><div class="tiny-progress"><span style="width:${progress}%"></span></div><span class="course-progress-text">${course.enrolled ? `${progress}% complete` : duration((course.lesson_count || 1) * 420)}</span><button class="course-arrow" type="button" data-open-course="${course.id}" aria-label="Open ${title}">→</button></div>
      </div></article>`;
  }

  function bindCourseButtons(parent = document) {
    $$('[data-open-course]', parent).forEach((button) => {
      button.addEventListener("click", () => openCourse(Number(button.dataset.openCourse)));
    });
  }

  async function loadCourses() {
    const { courses } = await api("/api/courses");
    state.courses = courses || [];
    const markup = state.courses.map(cardMarkup).join("") || "<p class=\"empty-state\">Courses will appear here soon.</p>";
    ["#homeCourseGrid", "#libraryCourseGrid", "#dashboardCourseGrid"].forEach((selector) => {
      const target = $(selector);
      if (target) target.innerHTML = markup;
    });
    $("#courseTotal").textContent = String(state.courses.length);
    $("#resultCount").textContent = String(state.courses.length);
    bindCourseButtons();
    renderDashboard();
  }

  function renderDashboard() {
    if (!state.user) return;
    const enrolled = state.courses.filter((course) => course.enrolled);
    const next = enrolled.find((course) => Number(course.progress_percent) < 100) || enrolled[0];
    const target = $("#continueCard");
    if (!target) return;
    target.innerHTML = next ? `<div><p class="eyebrow">CONTINUE LEARNING</p><h2>${escapeHtml(next.title)}</h2><p>${escapeHtml(next.summary)}</p><div class="progress-track"><span style="width:${next.progress_percent || 0}%"></span></div><small>${Math.round(next.progress_percent || 0)}% complete</small></div><button class="button" type="button" data-open-course="${next.id}">Continue <span aria-hidden="true">→</span></button>` : `<div><p class="eyebrow">YOUR LEARNING</p><h2>Choose a course to get started.</h2><p>Small, practical steps add up.</p></div><button class="button" type="button" data-route="library">Browse courses <span aria-hidden="true">→</span></button>`;
    bindCourseButtons(target);
    $$('[data-route="library"]', target).forEach((button) => button.addEventListener("click", () => setView("library")));
    const completed = state.courses.reduce((sum, course) => sum + Math.round((course.progress_percent || 0) / 100 * (course.lesson_count || 0)), 0);
    $("#lessonCount").textContent = String(completed);
    $("#learningMinutes").textContent = `${Math.max(0, completed * 7)} min`;
  }

  async function openCourse(courseId) {
    try {
      const { course } = await api(`/api/courses/${courseId}`);
      state.course = course;
      state.lesson = course.lessons?.[0] || null;
      setView("course");
      renderCourse();
    } catch (error) { toast(error.message, "error"); }
  }

  function renderCourse() {
    const course = state.course;
    if (!course) return;
    $("#course-title").textContent = course.title;
    $("#courseSideTitle").textContent = course.title;
    $("#courseChip").textContent = (course.category || "LEARNING").toUpperCase();
    $("#courseBreadcrumb").textContent = `${(course.category || "Learning").toUpperCase()} · ${course.lesson_count || 0} LESSONS`;
    $("#courseOverallText").textContent = `${Math.round((course.progress_percent || 0) / 100 * (course.lesson_count || 0))} of ${course.lesson_count || 0} lessons`;
    $("#courseOverallBar").style.width = `${course.progress_percent || 0}%`;
    $("#courseOverallPercent").textContent = `${Math.round(course.progress_percent || 0)}%`;
    const list = $("#lessonList");
    list.innerHTML = (course.lessons || []).map((lesson) => `<button class="lesson-list-item ${lesson.id === state.lesson?.id ? "active" : ""}" data-lesson-id="${lesson.id}" type="button"><span>${lesson.progress?.completed ? "✓" : lesson.position}</span><span><strong>${escapeHtml(lesson.title)}</strong><small>${duration(lesson.duration_seconds)}</small></span></button>`).join("") || "<p class=\"empty-state\">This course has no lessons yet.</p>";
    $$('[data-lesson-id]', list).forEach((button) => button.addEventListener("click", () => selectLesson(Number(button.dataset.lessonId))));
    $("#saveCourseButton").textContent = course.enrolled ? "✓ Enrolled" : "♡ Enroll in course";
    $("#saveCourseButton").onclick = () => enrollCourse(course.id);
    renderLesson();
  }

  async function selectLesson(id) {
    try {
      const { lesson } = await api(`/api/lessons/${id}`);
      state.lesson = lesson;
      state.quiz = null;
      renderCourse();
      if (lesson.quiz_id) loadQuiz(lesson.quiz_id);
    } catch (error) { toast(error.message, "error"); }
  }

  function lessonVideoSource(lesson) {
    const storedUrl = String(lesson?.video_url || "").trim();
    if (!storedUrl) return "";
    if (/^https?:\/\//i.test(storedUrl)) return storedUrl;
    return `/api/lessons/${lesson.id}/stream`;
  }

  function isHostedLessonVideo(lesson) {
    return /^https?:\/\//i.test(String(lesson?.video_url || "").trim());
  }

  function transcriptSummary(lesson) {
    const status = String(lesson?.transcription_status || "").toLowerCase();
    const failure = String(lesson?.transcription_error || "").trim();
    if (lesson?.transcript_access === false) {
      return {
        tone: "missing",
        availability: "Enroll to unlock the transcript",
        label: "Transcript available after enrollment",
        detail: "Enroll in this course to read its transcript and use Gemini for lesson-grounded learner questions.",
        button: "Enroll to unlock",
      };
    }
    if (hasText(lesson?.transcript)) {
      return {
        tone: "ready",
        availability: "Transcript ready · Gemini Q&A can use it",
        label: "Transcript ready",
        detail: "Gemini can use this lesson’s transcript to answer learner questions with lesson context.",
        button: "Read transcript",
      };
    }
    if (failure || /fail|error/.test(status)) {
      return {
        tone: "error",
        availability: "Transcript needs attention",
        label: "Transcript unavailable",
        detail: failure || "The transcript could not be prepared. Upload a transcript file or try local Whisper transcription again.",
        button: "Transcript status",
      };
    }
    if (/queue|process|transcrib|pending|running/.test(status)) {
      return {
        tone: "processing",
        availability: "Transcript is being prepared",
        label: "Transcription in progress",
        detail: "Local Whisper is preparing this transcript. Gemini Q&A can use it as soon as transcription finishes.",
        button: "Transcript processing",
      };
    }
    return {
      tone: "missing",
      availability: "No transcript yet",
      label: "Transcript not added",
      detail: "This lesson does not have a transcript yet, so Gemini Q&A cannot ground answers in this lesson’s content.",
      button: "Transcript status",
    };
  }

  function updatePlaybackUi(currentSeconds, totalSeconds) {
    const total = Number(totalSeconds) || Number(state.lesson?.duration_seconds) || 0;
    const current = Math.max(0, Number(currentSeconds) || 0);
    const percent = total ? Math.max(0, Math.min(100, Math.round((current / total) * 100))) : 0;
    $("#timelineFill").style.width = `${percent}%`;
    $("#videoTimeline").setAttribute("aria-valuenow", String(percent));
    $("#videoTime").textContent = `${formatClock(current)} / ${formatClock(total)}`;
    if (state.lesson) {
      state.lesson.progress = { ...(state.lesson.progress || {}), progress_percent: percent };
    }
    return percent;
  }

  function renderTranscript(lesson) {
    const summary = transcriptSummary(lesson);
    $("#transcriptText").textContent = hasText(lesson.transcript) ? lesson.transcript : "There is no transcript text to show yet.";
    $("#transcriptStatus").textContent = summary.label;
    $("#transcriptStatus").className = `transcript-status ${summary.tone}`;
    $("#transcriptDetail").textContent = summary.detail;
    $("#transcriptAvailability").textContent = summary.availability;
    $("#transcriptAvailability").className = `transcript-availability ${summary.tone}`;
    $("#transcriptButtonLabel").textContent = summary.button;
    const context = $("#tutorContextText");
    const tutorInput = $("#tutorInput");
    if (context) context.textContent = lesson.transcript_access === false
      ? "Enroll in this course to use its transcript with Gemini learner Q&A."
      : hasText(lesson.transcript)
      ? `Ask about “${lesson.title}”. Gemini will use this lesson’s transcript to keep its answer in context.`
      : summary.tone === "processing"
        ? "Local Whisper is still preparing this lesson’s transcript. Ask again once it is ready so Gemini can answer from the lesson content."
        : "No lesson transcript is ready yet. Add one so Gemini can answer questions using this lesson’s content.";
    if (tutorInput) tutorInput.placeholder = lesson.transcript_access === false
      ? "Enroll to unlock lesson questions…"
      : hasText(lesson.transcript) ? "Ask about this lesson’s transcript…" : "Ask a learning question…";
  }

  async function configureVideo(lesson) {
    const player = $("#lessonVideo");
    const frame = $("#videoFrame");
    const emptyState = $("#videoEmpty");
    const source = lessonVideoSource(lesson);
    const hasVideo = Boolean(source);
    const isHosted = isHostedLessonVideo(lesson);
    const lessonId = String(lesson.id);
    window.clearInterval(state.playback);
    if (player) {
      player.onloadedmetadata = null;
      player.ontimeupdate = null;
      player.onplay = null;
      player.onpause = null;
      player.onended = null;
      player.onerror = null;
      player.pause();
      player.dataset.lessonId = lessonId;
    }
    frame?.classList.toggle("has-video", hasVideo);
    frame?.classList.remove("is-playing");
    emptyState?.classList.toggle("is-hidden", hasVideo);
    player?.classList.toggle("is-hidden", !hasVideo);
    $("#videoPlayControl").disabled = !hasVideo;
    $("#playLessonButton").disabled = !hasVideo;
    $("#speedToggle").textContent = "1×";
    if (player) player.playbackRate = 1;
    if (!hasVideo) {
      if (player) {
        player.removeAttribute("src");
        player.dataset.source = "";
        player.load();
      }
      $("#videoSourceNotice").textContent = "No playable video has been added yet.";
      updatePlaybackUi(0, lesson.duration_seconds);
      return;
    }
    const savedPercent = Math.max(0, Math.min(100, Number(lesson.progress?.progress_percent) || 0));
    updatePlaybackUi((Number(lesson.duration_seconds) || 0) * savedPercent / 100, lesson.duration_seconds);
    if (!player) return;
    player.onloadedmetadata = () => {
      const total = Number.isFinite(player.duration) ? player.duration : lesson.duration_seconds;
      if (savedPercent > 0 && player.currentTime < 0.5 && total) player.currentTime = total * savedPercent / 100;
      updatePlaybackUi(player.currentTime, total);
    };
    player.ontimeupdate = () => updatePlaybackUi(player.currentTime, player.duration || lesson.duration_seconds);
    player.onplay = () => {
      frame?.classList.add("is-playing");
      $("#videoStatus").textContent = "Learning in progress…";
      $("#videoPlayControl").textContent = "❚❚";
      $("#playLessonButton").setAttribute("aria-label", "Pause lesson");
    };
    player.onpause = () => {
      frame?.classList.remove("is-playing");
      $("#videoPlayControl").textContent = "▶";
      $("#playLessonButton").setAttribute("aria-label", "Play lesson");
      if (!player.ended && state.course?.enrolled && player.currentTime > 0) saveProgress(updatePlaybackUi(player.currentTime, player.duration || lesson.duration_seconds));
    };
    player.onended = () => {
      frame?.classList.remove("is-playing");
      $("#videoStatus").textContent = "Lesson complete";
      $("#videoPlayControl").textContent = "▶";
      saveProgress(100);
    };
    player.onerror = () => {
      $("#videoStatus").textContent = "This video could not be played here.";
      $("#videoSourceNotice").textContent = isHosted ? "Check that the hosted URL is a direct, browser-playable video file." : "Your secure video session expired. Reopen the lesson and try again.";
    };

    const assignSource = () => {
      if (state.lesson?.id !== lesson.id || player.dataset.lessonId !== lessonId) return;
      player.dataset.source = source;
      player.src = source;
      player.load();
      $("#videoPlayControl").disabled = false;
      $("#playLessonButton").disabled = false;
      $("#videoSourceNotice").textContent = isHosted ? "Hosted video" : "Secure uploaded video";
      $("#videoStatus").textContent = lesson.progress?.completed ? "Lesson complete" : "Ready when you are";
    };

    if (isHosted) {
      assignSource();
      return;
    }
    const canUseSecurePlayback = Boolean(state.course?.enrolled || state.user?.role === "admin");
    if (!state.user || !canUseSecurePlayback) {
      player.removeAttribute("src");
      player.dataset.source = "";
      player.load();
      $("#videoPlayControl").disabled = true;
      $("#playLessonButton").disabled = true;
      $("#videoStatus").textContent = "Enroll to securely play this lesson";
      $("#videoSourceNotice").textContent = "A short-lived secure playback session is created after enrolment.";
      return;
    }
    player.removeAttribute("src");
    player.dataset.source = "";
    player.load();
    $("#videoPlayControl").disabled = true;
    $("#playLessonButton").disabled = true;
    $("#videoStatus").textContent = "Preparing secure playback…";
    $("#videoSourceNotice").textContent = "Creating a short-lived playback session…";
    try {
      await api(`/api/lessons/${lesson.id}/playback-ticket`);
      assignSource();
    } catch (error) {
      if (state.lesson?.id !== lesson.id || player.dataset.lessonId !== lessonId) return;
      $("#videoStatus").textContent = "Secure playback could not be prepared.";
      $("#videoSourceNotice").textContent = error.message;
    }
  }

  function renderLesson() {
    const lesson = state.lesson;
    if (!lesson) return;
    $("#lessonTitle").textContent = lesson.title;
    $("#lessonHeading").textContent = lesson.title;
    $("#lessonDescription").textContent = lesson.description || "Learn at your own pace, then try the reflection below.";
    $("#videoStatus").textContent = lesson.progress?.completed ? "Lesson complete" : (state.course?.enrolled ? "Ready when you are" : "Enroll to start this lesson");
    $("#completeLessonButton").textContent = lesson.progress?.completed ? "Completed ✓" : "Mark lesson complete ✓";
    $("#completeLessonButton").onclick = () => saveProgress(100);
    $("#playLessonButton").onclick = () => playLesson();
    $("#videoPlayControl").onclick = () => playLesson();
    renderTranscript(lesson);
    configureVideo(lesson);
    if (lesson.quiz_id) loadQuiz(lesson.quiz_id); else $("#lessonQuiz").classList.add("is-hidden");
  }

  async function enrollCourse(id) {
    if (!state.user) return setAuthMode("login");
    try {
      await api(`/api/courses/${id}/enroll`, { method: "POST" });
      await loadCourses();
      await openCourse(id);
      toast("You’re enrolled. Let’s begin!");
    } catch (error) { toast(error.message, "error"); }
  }

  async function saveProgress(percent) {
    if (!state.user) return setAuthMode("login");
    if (!state.course?.enrolled) return enrollCourse(state.course.id);
    try {
      const { progress } = await api("/api/progress", { method: "POST", body: JSON.stringify({ lesson_id: state.lesson.id, progress_percent: percent, watched_seconds: Math.round((state.lesson.duration_seconds || 0) * percent / 100) }) });
      state.lesson.progress = progress;
      state.course = (await api(`/api/courses/${state.course.id}`)).course;
      await loadCourses();
      renderCourse();
      toast(percent >= 100 ? "Great work — lesson complete!" : "Your progress is saved.");
    } catch (error) { toast(error.message, "error"); }
  }

  async function playLesson() {
    if (!state.user) return setAuthMode("login");
    if (!state.course?.enrolled && state.user?.role !== "admin") return enrollCourse(state.course.id);
    const player = $("#lessonVideo");
    if (!player || !lessonVideoSource(state.lesson)) return toast("This lesson does not have a playable video yet.", "error");
    try {
      if (player.paused) await player.play(); else player.pause();
    } catch {
      toast("Your browser could not start this video. Check the hosted video URL and try again.", "error");
    }
  }

  function seekLesson(event) {
    if (!state.user) return setAuthMode("login");
    if (!state.course?.enrolled && state.user?.role !== "admin") return enrollCourse(state.course.id);
    const player = $("#lessonVideo");
    if (!player || !Number.isFinite(player.duration) || player.duration <= 0) return;
    const timeline = $("#videoTimeline");
    const bounds = timeline.getBoundingClientRect();
    const position = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
    player.currentTime = player.duration * position;
    updatePlaybackUi(player.currentTime, player.duration);
  }

  function nudgeLessonPosition(direction) {
    const player = $("#lessonVideo");
    if (!player || !Number.isFinite(player.duration) || player.duration <= 0) return;
    const next = Math.max(0, Math.min(player.duration, player.currentTime + direction * 5));
    player.currentTime = next;
    updatePlaybackUi(next, player.duration);
  }

  function cyclePlaybackSpeed() {
    const player = $("#lessonVideo");
    if (!player || !lessonVideoSource(state.lesson)) return toast("Start a lesson video to change playback speed.", "error");
    const speeds = [1, 1.25, 1.5, 2];
    const currentIndex = speeds.findIndex((speed) => Math.abs(speed - player.playbackRate) < 0.01);
    const next = speeds[(currentIndex + 1) % speeds.length];
    player.playbackRate = next;
    $("#speedToggle").textContent = `${next}×`;
  }

  async function loadQuiz(id) {
    try {
      const { quiz } = await api(`/api/quizzes/${id}`);
      state.quiz = quiz;
      const quizRoot = $("#lessonQuiz");
      quizRoot.classList.remove("is-hidden");
      $("#quiz-title").textContent = quiz.title || "Quick reflection";
      $("#quizForm").innerHTML = (quiz.questions || []).map((item, index) => `<fieldset class="quiz-question"><legend>${index + 1}. ${escapeHtml(item.question)}</legend>${(item.options || []).map((option) => `<label><input type="radio" name="${escapeHtml(item.id)}" value="${escapeHtml(option)}" /> ${escapeHtml(option)}</label>`).join("")}</fieldset>`).join("") + `<button class="button" type="submit">Check my understanding <span aria-hidden="true">→</span></button>`;
      $("#quizForm").onsubmit = submitQuiz;
    } catch { $("#lessonQuiz").classList.add("is-hidden"); }
  }

  async function submitQuiz(event) {
    event.preventDefault();
    if (!state.user) return setAuthMode("login");
    const form = new FormData(event.currentTarget);
    const answers = Object.fromEntries(form.entries());
    if (Object.keys(answers).length < (state.quiz?.questions?.length || 0)) return toast("Choose an answer for each question.", "error");
    try {
      const { attempt } = await api(`/api/quizzes/${state.quiz.id}/submit`, { method: "POST", body: JSON.stringify({ answers }) });
      const feedback = $("#quizFeedback");
      feedback.classList.remove("is-hidden");
      feedback.innerHTML = `<strong>${attempt.score} of ${attempt.max_score} correct (${attempt.percentage}%)</strong>${(attempt.feedback || []).map((item) => `<p>${item.correct ? "✓" : "↗"} ${escapeHtml(item.explanation)}</p>`).join("")}`;
      toast("Your reflection has been saved.");
    } catch (error) { toast(error.message, "error"); }
  }

  async function openTutor() {
    if (!state.user) return setAuthMode("login");
    closeGeminiSettings();
    $("#tutorPanel").classList.add("is-open");
    $("#tutorPanel").setAttribute("aria-hidden", "false");
    syncOverlay();
    await refreshGeminiStatus();
    $("#tutorInput")?.focus();
  }

  function closeTutor() {
    $("#tutorPanel").classList.remove("is-open");
    $("#tutorPanel").setAttribute("aria-hidden", "true");
    syncOverlay();
  }

  async function askTutor(question) {
    const clean = question.trim();
    if (!clean) return;
    const messages = $("#tutorMessages");
    messages.insertAdjacentHTML("beforeend", `<div class="message message-user"><p>${escapeHtml(clean)}</p></div>`);
    if (!state.geminiConfigured) {
      messages.insertAdjacentHTML("beforeend", `<div class="message message-bot"><span class="message-mark" aria-hidden="true">✦</span><p>Add a Gemini API key in Session settings first. The key is used only for this signed-in session.</p></div>`);
      messages.scrollTop = messages.scrollHeight;
      return;
    }
    if (!state.lesson || !hasText(state.lesson.transcript)) {
      messages.insertAdjacentHTML("beforeend", `<div class="message message-bot"><span class="message-mark" aria-hidden="true">✦</span><p>Open a lesson with a ready transcript first. Gemini only answers from the selected lesson’s transcript so its response stays grounded in the course content.</p></div>`);
      messages.scrollTop = messages.scrollHeight;
      return;
    }
    const waiting = document.createElement("div");
    waiting.className = "message message-bot is-waiting";
    waiting.textContent = "Reading the lesson transcript…";
    messages.append(waiting); messages.scrollTop = messages.scrollHeight;
    try {
      const data = await api("/api/tutor", { method: "POST", body: JSON.stringify({ question: clean, course_id: state.course?.id, lesson_id: state.lesson?.id }) });
      waiting.outerHTML = `<div class="message message-bot"><span class="message-mark" aria-hidden="true">✦</span><p>${escapeHtml(data.reply)}</p></div>`;
    } catch (error) { waiting.outerHTML = `<div class="message message-bot"><p>${escapeHtml(error.message)}</p></div>`; }
    messages.scrollTop = messages.scrollHeight;
  }

  async function signIn(email, password) {
    const data = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
    state.token = data.token; state.user = data.user;
    localStorage.setItem("learnwithai_token", state.token);
    updateChrome(); await refreshGeminiStatus(); await loadCourses(); setView("dashboard"); toast(`Welcome back, ${state.user.name.split(" ")[0]}!`);
  }

  async function signOut() {
    try { if (state.token) await api("/api/session/gemini-key", { method: "DELETE" }); } catch { /* session expiry still clears the key */ }
    try { if (state.token) await api("/api/auth/logout", { method: "POST" }); } catch { /* local sign-out still succeeds */ }
    window.clearInterval(state.playback); state.token = ""; state.user = null; state.course = null; state.lesson = null; state.geminiConfigured = false; state.uploadLimits = null;
    localStorage.removeItem("learnwithai_token"); updateChrome(); await loadCourses(); setView("home"); toast("You’ve been signed out.");
  }

  function isHostedVideoUrl(value) {
    try {
      const url = new URL(String(value || "").trim());
      return url.protocol === "https:";
    } catch {
      return false;
    }
  }

  function isTranscriptFile(file) {
    return file instanceof File && file.size > 0 && /\.(txt|srt|vtt)$/i.test(file.name || "");
  }

  const uploadLimitLabel = (bytes) => `${Number((bytes / (1024 * 1024)).toFixed(2))} MiB`;

  function uploadSizeError(videoFile, transcriptFile) {
    if (!state.uploadLimits) return "";
    if (videoFile instanceof File && videoFile.size > state.uploadLimits.max_video_bytes) {
      return `This video exceeds the ${uploadLimitLabel(state.uploadLimits.max_video_bytes)} upload limit. Choose a smaller file or use a hosted video URL.`;
    }
    if (transcriptFile instanceof File && transcriptFile.size > state.uploadLimits.max_transcript_bytes) {
      return `This transcript exceeds the ${uploadLimitLabel(state.uploadLimits.max_transcript_bytes)} upload limit. Choose a smaller transcript file.`;
    }
    return "";
  }

  async function refreshUploadLimits() {
    const limits = await api("/api/admin/upload-limits");
    if (![limits.max_video_bytes, limits.max_transcript_bytes].every((value) => Number.isSafeInteger(value) && value > 0)) {
      throw new Error("Could not check upload limits. Refresh the page and try again.");
    }
    state.uploadLimits = limits;
    const hint = $("#uploadLimitsHint");
    if (hint) hint.textContent = `Upload limits: ${uploadLimitLabel(limits.max_video_bytes)} per video and ${uploadLimitLabel(limits.max_transcript_bytes)} per transcript.`;
    syncMediaSourceControls();
    return limits;
  }

  function transcriptTextFromFile(rawText) {
    return String(rawText || "")
      .replace(/^\uFEFF?WEBVTT[^\n]*\n?/i, "")
      .replace(/^\d+\s*$/gm, "")
      .replace(/^\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s+-->\s+\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}.*$/gm, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  async function readHostedTranscript(file) {
    if (!isTranscriptFile(file)) throw new Error("Choose a .txt, .srt, or .vtt transcript file.");
    const transcript = transcriptTextFromFile(await file.text());
    if (!transcript) throw new Error("That transcript file does not contain readable text.");
    if (transcript.length > 200000) throw new Error("The usable transcript must be 200,000 characters or fewer.");
    return transcript;
  }

  function setPublishingState(publishing, message = "") {
    const publishButton = $("#publishCourseButton");
    if (publishButton) {
      publishButton.disabled = publishing;
      publishButton.innerHTML = publishing ? "Publishing…" : "Publish course <span aria-hidden=\"true\">→</span>";
    }
    if (message) $("#courseSaveStatus").textContent = message;
  }

  async function createCourse(event) {
    event.preventDefault();
    const formElement = $("#courseCreateForm");
    if (!formElement) return;
    const form = new FormData(formElement);
    const description = String(form.get("description") || "").trim();
    const videoFile = form.get("videoFile");
    const hasVideoFile = videoFile instanceof File && videoFile.size > 0;
    const hostedUrl = String(form.get("videoUrl") || "").trim();
    const transcriptMode = String(form.get("transcriptionMode") || "none");
    const transcriptFile = form.get("transcriptFile");
    const payload = {
      title: form.get("title"),
      summary: description.slice(0, 280),
      description: description.length >= 20 ? description : `${description} This course gives learners a clear practical starting point.`,
      category: form.get("topic"),
      published: true,
    };
    try {
      if (!hasVideoFile && !hostedUrl) throw new Error("Add a video file or a hosted direct video URL before publishing this lesson.");
      if (hasVideoFile && hostedUrl) throw new Error("Choose either a video file or a hosted direct URL, not both.");
      if (hasVideoFile && !String(videoFile.type || "").startsWith("video/")) throw new Error("Choose a browser-ready video file.");
      if (hostedUrl && !isHostedVideoUrl(hostedUrl)) throw new Error("Use a direct HTTPS URL for the hosted video.");
      if (transcriptMode === "auto" && !hasVideoFile) throw new Error("Local Whisper transcription needs a video file uploaded to LearnWithAI. For a hosted video, upload your transcript instead.");
      if (transcriptMode === "upload" && !isTranscriptFile(transcriptFile)) throw new Error("Choose a .txt, .srt, or .vtt transcript file.");
      setPublishingState(true, "Checking upload limits…");
      if (hasVideoFile || transcriptMode === "upload") {
        await refreshUploadLimits();
        const sizeError = uploadSizeError(videoFile, transcriptMode === "upload" ? transcriptFile : null);
        if (sizeError) throw new Error(sizeError);
      }
      const hostedTranscript = transcriptMode === "upload" && !hasVideoFile ? await readHostedTranscript(transcriptFile) : "";
      setPublishingState(true, hasVideoFile ? "Creating course and uploading video…" : "Creating course…");
      const { course } = await api("/api/admin/courses", { method: "POST", body: JSON.stringify(payload) });
      const minutes = Math.max(1, Math.min(1440, Number.parseInt(String(form.get("duration") || ""), 10) || 10));
      const lessonResponse = await api(`/api/courses/${course.id}/lessons`, { method: "POST", body: JSON.stringify({
        title: form.get("firstLesson"),
        description: payload.description,
        video_url: hostedUrl || null,
        duration_seconds: minutes * 60,
        position: 1,
        transcript: hostedTranscript,
      }) });
      const lesson = lessonResponse.lesson;
      if (hasVideoFile) {
        const uploadData = new FormData();
        uploadData.append("video", videoFile);
        uploadData.append("transcription_mode", transcriptMode);
        if (transcriptMode === "upload") uploadData.append("transcript_file", transcriptFile);
        setPublishingState(true, transcriptMode === "auto" ? "Uploading video and starting local Whisper transcription…" : "Uploading lesson video…");
        await api(`/api/lessons/${lesson.id}/video`, { method: "POST", body: uploadData });
      }
      const transcriptMessage = transcriptMode === "auto" ? " Local Whisper is preparing the transcript." : transcriptMode === "upload" ? " The transcript is ready for Gemini Q&A." : " Add a transcript later to enable grounded Gemini answers.";
      $("#courseSaveStatus").textContent = `Published “${course.title}” with its first lesson.${transcriptMessage}`;
      formElement.reset();
      syncMediaSourceControls();
      await loadCourses();
      toast("Course published.", "success");
    } catch (error) {
      $("#courseSaveStatus").textContent = error.message;
      toast(error.message, "error");
    } finally {
      setPublishingState(false);
    }
  }

  function fileLabel(file, fallback) {
    if (!(file instanceof File) || !file.size) return fallback;
    const size = file.size >= 1024 * 1024 ? `${(file.size / (1024 * 1024)).toFixed(1)} MB` : `${Math.ceil(file.size / 1024)} KB`;
    return `${file.name} · ${size}`;
  }

  function syncMediaSourceControls() {
    const videoFileInput = $("#lessonVideoFile");
    const videoUrlInput = $("#lessonVideoUrl");
    const transcriptInput = $("#lessonTranscriptFile");
    if (!videoFileInput || !videoUrlInput || !transcriptInput) return;
    const videoFile = videoFileInput.files?.[0];
    const hostedUrl = videoUrlInput.value.trim();
    const hasVideoFile = Boolean(videoFile?.size);
    const hasHostedUrl = Boolean(hostedUrl);
    const autoOption = $("input[name='transcriptionMode'][value='auto']");
    const uploadOption = $("input[name='transcriptionMode'][value='upload']");
    const noneOption = $("input[name='transcriptionMode'][value='none']");
    const autoRequiresUpload = hasHostedUrl && !hasVideoFile;
    autoOption.disabled = autoRequiresUpload;
    autoOption.closest(".transcription-choice")?.classList.toggle("is-disabled", autoRequiresUpload);
    if (autoRequiresUpload && autoOption.checked) (transcriptInput.files?.[0]?.size ? uploadOption : noneOption).checked = true;
    const activeMode = $("input[name='transcriptionMode']:checked")?.value || "none";
    transcriptInput.disabled = activeMode !== "upload";
    $("#transcriptFileField")?.classList.toggle("is-hidden", activeMode !== "upload");
    $("#videoFileName").textContent = fileLabel(videoFile, "MP4, WebM, or another browser-ready video");
    $("#transcriptFileName").textContent = fileLabel(transcriptInput.files?.[0], "Choose a text, SRT, or VTT file.");
    const hint = $("#mediaSourceHint");
    if (!hint) return;
    const sizeError = uploadSizeError(videoFile, activeMode === "upload" ? transcriptInput.files?.[0] : null);
    if (sizeError) {
      hint.textContent = sizeError;
      return;
    }
    if (hasVideoFile) {
      hint.textContent = activeMode === "auto"
        ? "Your video will upload to LearnWithAI, then local faster-whisper will prepare its transcript. No Gemini API key is needed."
        : "Your video will upload to LearnWithAI. Learners will stream it from the course page.";
    } else if (hasHostedUrl) {
      hint.textContent = activeMode === "upload"
        ? "The hosted video will use the transcript you upload with this course."
        : "Hosted videos need a transcript file for Gemini-grounded lesson answers.";
    } else {
      hint.textContent = "Choose a video source to enable the matching transcript options.";
    }
  }

  function filterCourses() {
    const query = $("#courseSearch")?.value.trim().toLowerCase() || "";
    const category = $$('input[name="topic"]:checked').find((input) => input.value !== "all")?.value || "";
    const matches = state.courses.filter((course) => !query || `${course.title} ${course.summary} ${course.category}`.toLowerCase().includes(query)).filter((course) => !category || course.category.toLowerCase().includes(category.replace("health", "community").replace("livelihoods", "")));
    $("#libraryCourseGrid").innerHTML = matches.map(cardMarkup).join("");
    $("#resultCount").textContent = String(matches.length); $("#emptyCourses").classList.toggle("is-hidden", matches.length > 0);
    bindCourseButtons($("#libraryCourseGrid"));
  }

  function attachEvents() {
    $$('[data-route]').forEach((element) => element.addEventListener("click", (event) => {
      const route = element.dataset.route;
      if (!route) return; event.preventDefault();
      if ((route === "dashboard" || route === "admin") && !state.user) return setAuthMode("login");
      if (route === "admin" && state.user?.role !== "admin") return toast("Admin access is required.", "error");
      setView(route);
    }));
    $$('[data-auth-mode]').forEach((element) => element.addEventListener("click", () => setAuthMode(element.dataset.authMode)));
    $$('[data-demo]').forEach((element) => element.addEventListener("click", () => signIn(element.dataset.demo === "admin" ? "admin@learnwithai.demo" : "learner@learnwithai.demo", "DemoPass123!").catch((error) => toast(error.message, "error"))));
    $("#loginForm")?.addEventListener("submit", (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); signIn(data.get("email"), data.get("password")).catch((error) => toast(error.message, "error")); });
    $("#registerForm")?.addEventListener("submit", async (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); if (!data.get("terms")) return toast("Please accept the community guidelines to continue.", "error"); try { const response = await api("/api/auth/register", { method: "POST", body: JSON.stringify({ name: data.get("name"), email: data.get("email"), password: data.get("password") }) }); state.token = response.token; state.user = response.user; localStorage.setItem("learnwithai_token", state.token); updateChrome(); await refreshGeminiStatus(); await loadCourses(); setView("dashboard"); toast("Your account is ready."); } catch (error) { toast(error.message, "error"); } });
    $$(".password-toggle").forEach((button) => button.addEventListener("click", () => { const input = $("input", button.parentElement); const show = input.type === "password"; input.type = show ? "text" : "password"; button.textContent = show ? "Hide" : "Show"; }));
    $("#profileButton")?.addEventListener("click", () => $("#profileMenu")?.classList.toggle("is-hidden"));
    ["#signOutButton", "#sideSignOut", "#adminSignOut"].forEach((selector) => $(selector)?.addEventListener("click", signOut));
    $("#menuToggle")?.addEventListener("click", () => { const open = $("#mainNav").classList.toggle("is-open"); $("#menuToggle").setAttribute("aria-expanded", String(open)); });
    $$('[data-gemini-settings]').forEach((button) => button.addEventListener("click", openGeminiSettings));
    $("#geminiKeyForm")?.addEventListener("submit", saveGeminiKey);
    $("#removeGeminiKey")?.addEventListener("click", removeGeminiKey);
    $("#closeGeminiPanel")?.addEventListener("click", closeGeminiSettings);
    $$('[data-tutor-open]').forEach((button) => button.addEventListener("click", openTutor));
    $("#closeTutor")?.addEventListener("click", closeTutor); $("#overlay")?.addEventListener("click", () => { closeTutor(); closeGeminiSettings(); });
    $("#tutorForm")?.addEventListener("submit", (event) => { event.preventDefault(); const input = $("#tutorInput"); askTutor(input.value); input.value = ""; });
    $$("#tutorSuggestions button").forEach((button) => button.addEventListener("click", () => askTutor(button.textContent)));
    $("#transcriptButton")?.addEventListener("click", () => { if (state.lesson?.transcript_access === false && state.course) return enrollCourse(state.course.id); $("#transcriptPanel")?.classList.remove("is-hidden"); $("#captionToggle")?.setAttribute("aria-pressed", "true"); $("#captionToggle")?.setAttribute("aria-label", "Hide transcript"); });
    $("#closeTranscript")?.addEventListener("click", () => { $("#transcriptPanel")?.classList.add("is-hidden"); $("#captionToggle")?.setAttribute("aria-pressed", "false"); $("#captionToggle")?.setAttribute("aria-label", "Show transcript"); });
    $("#captionToggle")?.addEventListener("click", () => { const panel = $("#transcriptPanel"); const open = panel?.classList.toggle("is-hidden") === false; $("#captionToggle").setAttribute("aria-pressed", String(open)); $("#captionToggle").setAttribute("aria-label", open ? "Hide transcript" : "Show transcript"); });
    $("#speedToggle")?.addEventListener("click", cyclePlaybackSpeed);
    $("#videoTimeline")?.addEventListener("click", seekLesson);
    $("#videoTimeline")?.addEventListener("keydown", (event) => { if (event.key === "ArrowRight" || event.key === "ArrowUp") { event.preventDefault(); nudgeLessonPosition(1); } if (event.key === "ArrowLeft" || event.key === "ArrowDown") { event.preventDefault(); nudgeLessonPosition(-1); } });
    $("#courseCreateForm")?.addEventListener("submit", createCourse);
    $("#courseCreateForm")?.addEventListener("reset", () => window.setTimeout(syncMediaSourceControls, 0));
    $("#newCourseFocus")?.addEventListener("click", () => $("#courseCreateForm [name='title']")?.focus());
    $("#lessonVideoFile")?.addEventListener("change", () => { if ($("#lessonVideoFile").files?.[0]?.size) $("#lessonVideoUrl").value = ""; syncMediaSourceControls(); });
    $("#lessonVideoUrl")?.addEventListener("input", () => { if ($("#lessonVideoUrl").value.trim()) $("#lessonVideoFile").value = ""; syncMediaSourceControls(); });
    $$("input[name='transcriptionMode']").forEach((input) => input.addEventListener("change", syncMediaSourceControls));
    $("#lessonTranscriptFile")?.addEventListener("change", () => { if ($("#lessonTranscriptFile").files?.[0]?.size) $("input[name='transcriptionMode'][value='upload']").checked = true; syncMediaSourceControls(); });
    syncMediaSourceControls();
    $("#courseSearch")?.addEventListener("input", filterCourses); $$('input[name="topic"], input[name="level"], #courseSort').forEach((input) => input.addEventListener("change", filterCourses));
    $("#clearFilters")?.addEventListener("click", () => { $$('input[name="topic"], input[name="level"]').forEach((input) => { input.checked = input.value === "all"; }); $("#courseSearch").value = ""; filterCourses(); });
    $("#forgotPassword")?.addEventListener("click", () => toast("Password reset can be added when email delivery is configured."));
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeTutor(); closeGeminiSettings(); } });
  }

  async function boot() {
    attachEvents();
    try {
      if (state.token) state.user = (await api("/api/me")).user;
    } catch { localStorage.removeItem("learnwithai_token"); state.token = ""; }
    updateChrome();
    await refreshGeminiStatus();
    try { await loadCourses(); } catch (error) { toast(error.message, "error"); }
    setView(state.user ? "dashboard" : "home", { scroll: false });
  }

  boot();
})();
