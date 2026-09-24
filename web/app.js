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
    if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch(path, { ...options, headers });
    } catch {
      throw new Error("Could not reach LearnWithAI. Check that the local server is running.");
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

  function renderLesson() {
    const lesson = state.lesson;
    if (!lesson) return;
    $("#lessonTitle").textContent = lesson.title;
    $("#lessonHeading").textContent = lesson.title;
    $("#lessonDescription").textContent = lesson.description || "Learn at your own pace, then try the reflection below.";
    $("#transcriptText").textContent = lesson.transcript || "A transcript will be available for this lesson.";
    $("#videoTime").textContent = `00:00 / ${String(Math.floor((lesson.duration_seconds || 0) / 60)).padStart(2, "0")}:${String((lesson.duration_seconds || 0) % 60).padStart(2, "0")}`;
    const percent = Math.round(lesson.progress?.progress_percent || 0);
    $("#timelineFill").style.width = `${percent}%`;
    $("#videoTimeline").setAttribute("aria-valuenow", String(percent));
    $("#videoStatus").textContent = lesson.progress?.completed ? "Lesson complete" : (state.course?.enrolled ? "Ready when you are" : "Enroll to start this lesson");
    $("#completeLessonButton").textContent = lesson.progress?.completed ? "Completed ✓" : "Mark lesson complete ✓";
    $("#completeLessonButton").onclick = () => saveProgress(100);
    $("#playLessonButton").onclick = () => playLesson();
    $("#videoPlayControl").onclick = () => playLesson();
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

  function playLesson() {
    if (!state.user) return setAuthMode("login");
    if (!state.course?.enrolled) return enrollCourse(state.course.id);
    window.clearInterval(state.playback);
    let value = Number(state.lesson.progress?.progress_percent || 0);
    $("#videoStatus").textContent = "Learning in progress…";
    $("#playLessonButton").classList.add("is-playing");
    state.playback = window.setInterval(() => {
      value = Math.min(100, value + 2);
      $("#timelineFill").style.width = `${value}%`;
      $("#videoTimeline").setAttribute("aria-valuenow", String(value));
      if (value >= 100) { window.clearInterval(state.playback); saveProgress(100); }
    }, 280);
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

  function openTutor() {
    if (!state.user) return setAuthMode("login");
    $("#tutorPanel").classList.add("is-open");
    $("#tutorPanel").setAttribute("aria-hidden", "false");
    $("#overlay").classList.add("is-visible");
    $("#tutorInput")?.focus();
  }

  function closeTutor() {
    $("#tutorPanel").classList.remove("is-open");
    $("#tutorPanel").setAttribute("aria-hidden", "true");
    $("#overlay").classList.remove("is-visible");
  }

  async function askTutor(question) {
    const clean = question.trim();
    if (!clean) return;
    const messages = $("#tutorMessages");
    messages.insertAdjacentHTML("beforeend", `<div class="message message-user"><p>${escapeHtml(clean)}</p></div>`);
    const waiting = document.createElement("div");
    waiting.className = "message message-bot is-waiting";
    waiting.textContent = "Thinking with you…";
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
    updateChrome(); await loadCourses(); setView("dashboard"); toast(`Welcome back, ${state.user.name.split(" ")[0]}!`);
  }

  async function signOut() {
    try { if (state.token) await api("/api/auth/logout", { method: "POST" }); } catch { /* local sign-out still succeeds */ }
    window.clearInterval(state.playback); state.token = ""; state.user = null; state.course = null; state.lesson = null;
    localStorage.removeItem("learnwithai_token"); updateChrome(); await loadCourses(); setView("home"); toast("You’ve been signed out.");
  }

  async function createCourse(event) {
    event.preventDefault();
    const formElement = $("#courseCreateForm");
    if (!formElement) return;
    const form = new FormData(formElement);
    const description = String(form.get("description") || "").trim();
    const payload = { title: form.get("title"), summary: description.slice(0, 280), description: description.length >= 20 ? description : `${description} This course gives learners a clear practical starting point.`, category: form.get("topic"), published: true };
    try {
      const { course } = await api("/api/admin/courses", { method: "POST", body: JSON.stringify(payload) });
      const minutes = Math.max(1, Math.min(1440, Number.parseInt(String(form.get("duration") || ""), 10) || 10));
      await api(`/api/courses/${course.id}/lessons`, { method: "POST", body: JSON.stringify({
        title: form.get("firstLesson"),
        description: payload.description,
        video_url: String(form.get("videoUrl") || "").trim() || null,
        duration_seconds: minutes * 60,
        position: 1,
        transcript: "",
      }) });
      $("#courseSaveStatus").textContent = `Published “${course.title}” with its first lesson.`;
      formElement.reset(); await loadCourses(); toast("Course published.");
    } catch (error) { $("#courseSaveStatus").textContent = error.message; }
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
    $("#registerForm")?.addEventListener("submit", async (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); if (!data.get("terms")) return toast("Please accept the community guidelines to continue.", "error"); try { const response = await api("/api/auth/register", { method: "POST", body: JSON.stringify({ name: data.get("name"), email: data.get("email"), password: data.get("password") }) }); state.token = response.token; state.user = response.user; localStorage.setItem("learnwithai_token", state.token); updateChrome(); await loadCourses(); setView("dashboard"); toast("Your account is ready."); } catch (error) { toast(error.message, "error"); } });
    $$(".password-toggle").forEach((button) => button.addEventListener("click", () => { const input = $("input", button.parentElement); const show = input.type === "password"; input.type = show ? "text" : "password"; button.textContent = show ? "Hide" : "Show"; }));
    $("#profileButton")?.addEventListener("click", () => $("#profileMenu")?.classList.toggle("is-hidden"));
    ["#signOutButton", "#sideSignOut", "#adminSignOut"].forEach((selector) => $(selector)?.addEventListener("click", signOut));
    $("#menuToggle")?.addEventListener("click", () => { const open = $("#mainNav").classList.toggle("is-open"); $("#menuToggle").setAttribute("aria-expanded", String(open)); });
    $$('[data-tutor-open]').forEach((button) => button.addEventListener("click", openTutor));
    $("#closeTutor")?.addEventListener("click", closeTutor); $("#overlay")?.addEventListener("click", closeTutor);
    $("#tutorForm")?.addEventListener("submit", (event) => { event.preventDefault(); const input = $("#tutorInput"); askTutor(input.value); input.value = ""; });
    $$("#tutorSuggestions button").forEach((button) => button.addEventListener("click", () => askTutor(button.textContent)));
    $("#transcriptButton")?.addEventListener("click", () => $("#transcriptPanel")?.classList.remove("is-hidden")); $("#closeTranscript")?.addEventListener("click", () => $("#transcriptPanel")?.classList.add("is-hidden"));
    $("#courseCreateForm")?.addEventListener("submit", createCourse); $("#newCourseFocus")?.addEventListener("click", () => $("#courseCreateForm input")?.focus());
    $("#courseSearch")?.addEventListener("input", filterCourses); $$('input[name="topic"], input[name="level"], #courseSort').forEach((input) => input.addEventListener("change", filterCourses));
    $("#clearFilters")?.addEventListener("click", () => { $$('input[name="topic"], input[name="level"]').forEach((input) => { input.checked = input.value === "all"; }); $("#courseSearch").value = ""; filterCourses(); });
    $("#forgotPassword")?.addEventListener("click", () => toast("Password reset can be added when email delivery is configured."));
  }

  async function boot() {
    attachEvents();
    try {
      if (state.token) state.user = (await api("/api/me")).user;
    } catch { localStorage.removeItem("learnwithai_token"); state.token = ""; }
    updateChrome();
    try { await loadCourses(); } catch (error) { toast(error.message, "error"); }
    setView(state.user ? "dashboard" : "home", { scroll: false });
  }

  boot();
})();
