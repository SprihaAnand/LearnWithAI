"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const MiB = 1024 * 1024;
const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
assert.ok(source.includes("\n  boot();\n"), "Application entry point is available to the DOM harness");
const testSource = source.replace("\n  boot();\n", "\n  globalThis.uploadUi = { createCourse, state };\n");

class TestFile {
  constructor(name, size, type = "video/mp4") {
    Object.assign(this, { name, size, type });
  }
  async text() { return "A reviewed lesson transcript."; }
}

class TestFormData {
  constructor(form) { this.values = new Map(Object.entries(form?.values || {})); }
  get(name) { return this.values.get(name) ?? null; }
  append(name, value) { this.values.set(name, value); }
}

function harness({ values = {}, limits = { max_video_bytes: 100 * MiB, max_transcript_bytes: 5 * MiB }, failPath } = {}) {
  const requests = [];
  const form = {
    values: {
      title: "Video course", description: "A practical introduction to community learning.",
      topic: "community", firstLesson: "First lesson", duration: "10",
      videoFile: new TestFile("lesson.mp4", MiB), transcriptionMode: "auto", ...values,
    },
    resetCount: 0,
    reset() { this.resetCount += 1; },
  };
  const nodes = new Map([
    ["#courseCreateForm", form], ["#courseSaveStatus", {}], ["#publishCourseButton", {}],
    ["#uploadLimitsHint", {}], ["#courseTotal", {}], ["#resultCount", {}],
    ["#toastRegion", { append() {} }],
  ]);
  const context = vm.createContext({
    File: TestFile, FormData: TestFormData, URL,
    localStorage: { getItem() { return "test-session"; } },
    window: { setTimeout() {} },
    document: {
      querySelector(selector) { return nodes.get(selector) || null; },
      querySelectorAll() { return []; },
      createElement() { return { remove() {} }; },
    },
    async fetch(url, options) {
      requests.push({ url, ...options });
      if (url === failPath) throw new TypeError("Failed to fetch");
      let data;
      if (url === "/api/admin/upload-limits") data = limits;
      else if (url === "/api/admin/courses") data = { course: { id: 8, title: "Video course" } };
      else if (url === "/api/courses/8/lessons") data = { lesson: { id: 15 } };
      else if (url === "/api/lessons/15/video") data = { lesson: { id: 15 } };
      else if (url === "/api/courses") data = { courses: [] };
      else throw new Error(`Unexpected request: ${url}`);
      return { ok: true, async json() { return data; } };
    },
  });
  vm.runInContext(testSource, context, { filename: "web/app.js" });
  context.uploadUi.state.user = { role: "admin", name: "Test Admin" };
  return {
    form, requests, nodes,
    submit: () => context.uploadUi.createCourse({ preventDefault() {} }),
    status: () => nodes.get("#courseSaveStatus").textContent,
  };
}

function assertStoppedBeforeCreation(ui) {
  assert.equal(ui.requests.some((request) => request.method === "POST"), false);
  assert.equal(ui.form.resetCount, 0, "Form inputs must survive rejection");
  assert.equal(ui.nodes.get("#publishCourseButton").disabled, false);
}

test("oversized video is rejected before creating a course or transmitting its bytes", async () => {
  const ui = harness({ values: { videoFile: new TestFile("large.mp4", 100 * MiB + 1) } });
  await ui.submit();
  assert.match(ui.status(), /video exceeds the 100 MiB upload limit/);
  assertStoppedBeforeCreation(ui);
});

test("a lower server upload limit is respected and displayed", async () => {
  const ui = harness({
    limits: { max_video_bytes: 2 * MiB, max_transcript_bytes: MiB },
    values: { videoFile: new TestFile("lesson.mp4", 3 * MiB) },
  });
  await ui.submit();
  assert.match(ui.status(), /video exceeds the 2 MiB upload limit/);
  assert.match(ui.nodes.get("#uploadLimitsHint").textContent, /2 MiB per video and 1 MiB per transcript/);
  assertStoppedBeforeCreation(ui);
});

for (const hosted of [false, true]) {
  test(`oversized transcript is rejected for ${hosted ? "hosted" : "uploaded"} video before creation`, async () => {
    const ui = harness({ values: {
      ...(hosted ? { videoFile: null, videoUrl: "https://example.org/lesson.mp4" } : {}),
      transcriptionMode: "upload", transcriptFile: new TestFile("lesson.txt", 5 * MiB + 1, "text/plain"),
    } });
    await ui.submit();
    assert.match(ui.status(), /transcript exceeds the 5 MiB upload limit/);
    assertStoppedBeforeCreation(ui);
  });
}

test("files at their exact limits publish successfully with browser-generated multipart headers", async () => {
  const ui = harness({ values: {
    videoFile: new TestFile("lesson.mp4", 100 * MiB), transcriptionMode: "upload",
    transcriptFile: new TestFile("lesson.txt", 5 * MiB, "text/plain"),
  } });
  await ui.submit();
  assert.equal(ui.requests[0].url, "/api/admin/upload-limits");
  const upload = ui.requests.find((request) => request.url === "/api/lessons/15/video");
  assert.ok(upload.body instanceof TestFormData);
  assert.equal(upload.headers["Content-Type"], undefined);
  assert.equal(upload.headers.Authorization, "Bearer test-session");
  assert.equal(ui.form.resetCount, 1);
  assert.match(ui.status(), /Published/);
});

test("unavailable upload limits stop publishing before course creation", async () => {
  const ui = harness({ failPath: "/api/admin/upload-limits" });
  await ui.submit();
  assert.match(ui.status(), /Could not connect/);
  assertStoppedBeforeCreation(ui);
});

test("invalid server limits stop publishing before course creation", async () => {
  const ui = harness({ limits: { max_video_bytes: 0, max_transcript_bytes: "500" } });
  await ui.submit();
  assert.match(ui.status(), /Could not check upload limits/);
  assertStoppedBeforeCreation(ui);
});

test("interrupted upload preserves inputs and reports the upload problem", async () => {
  const ui = harness({ failPath: "/api/lessons/15/video" });
  await ui.submit();
  assert.match(ui.status(), /video upload connection was interrupted/);
  assert.doesNotMatch(ui.status(), /local server is running/);
  assert.equal(ui.form.resetCount, 0);
  assert.equal(ui.nodes.get("#publishCourseButton").disabled, false);
});
