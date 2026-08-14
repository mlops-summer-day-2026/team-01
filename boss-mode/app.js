const STORAGE_KEY = "stand-manager-boss-mode-v1";
const taskButtons = [...document.querySelectorAll(".task")];
const progressBar = document.querySelector("#progress-bar");
const progressLabel = document.querySelector("#progress-label");
const video = document.querySelector("#escape-video");

let completed = new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"));

function speak(text) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const message = new SpeechSynthesisUtterance(text);
  message.lang = "ru-RU";
  message.rate = 0.93;
  message.pitch = 0.88;
  const voices = window.speechSynthesis.getVoices();
  message.voice = voices.find((voice) => voice.lang.toLowerCase().startsWith("ru")) || null;
  window.speechSynthesis.speak(message);
}

function render() {
  taskButtons.forEach((button) => {
    const done = completed.has(button.dataset.id);
    button.classList.toggle("done", done);
    button.setAttribute("aria-pressed", String(done));
  });
  const count = completed.size;
  progressLabel.textContent = `${count} / ${taskButtons.length}`;
  progressBar.style.width = `${(count / taskButtons.length) * 100}%`;
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...completed]));
}

function celebrate() {
  const colors = ["#ffb52e", "#ff7a1a", "#48f0aa", "#9a6cff", "#fff3d6"];
  const holder = document.querySelector("#confetti");
  for (let index = 0; index < 70; index += 1) {
    const piece = document.createElement("i");
    piece.className = "confetti-piece";
    piece.style.left = `${Math.random() * 100}%`;
    piece.style.background = colors[index % colors.length];
    piece.style.animationDelay = `${Math.random() * .8}s`;
    piece.style.setProperty("--drift", `${(Math.random() - .5) * 260}px`);
    holder.append(piece);
    setTimeout(() => piece.remove(), 3800);
  }
  speak("Спринт закрыт. Билеты на Бали согласованы. Босс режим выполнен!");
}

document.querySelector("#unlock").addEventListener("click", () => {
  document.body.classList.remove("locked");
  document.body.classList.add("unlocked");
  document.querySelector(".app-shell").setAttribute("aria-hidden", "false");
  video.play().catch(() => {});
  speak("Босс режим активирован. Секретный план загружен.");
});

taskButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const id = button.dataset.id;
    completed.has(id) ? completed.delete(id) : completed.add(id);
    render();
    if (completed.size === taskButtons.length) celebrate();
  });
});

document.querySelector("#speak").addEventListener("click", () => {
  speak("План команды один. Уехать на Бали. Уволиться. Вернуться. И всё-таки доделать спринт.");
});

document.querySelector("#reset").addEventListener("click", () => {
  completed = new Set();
  render();
  speak("План сброшен. Начинаем сначала.");
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    document.body.classList.add("locked");
    document.body.classList.remove("unlocked");
    document.querySelector(".app-shell").setAttribute("aria-hidden", "true");
    video.pause();
    window.speechSynthesis?.cancel();
  }
});

render();
