(() => {
	const root = document.documentElement;
	const readSavedTheme = () => {
		try {
			return localStorage.getItem("cintac-theme");
		} catch (error) {
			console.warn("No se pudo leer la preferencia del tema.", error);
			return null;
		}
	};
	const savedTheme = readSavedTheme();
	const followsDarkPreference = typeof window.matchMedia === "function"
		&& window.matchMedia("(prefers-color-scheme: dark)").matches;
	const initialTheme = savedTheme === "light" || savedTheme === "dark"
		? savedTheme
		: (followsDarkPreference ? "dark" : "light");
	root.dataset.bsTheme = initialTheme;

	const updateSwitch = (input) => {
		const isDark = root.dataset.bsTheme === "dark";
		input.checked = isDark;
		input.setAttribute("aria-checked", String(isDark));
		const label = input.closest(".theme-toggle")?.querySelector(".theme-toggle-label");
		if (label) {
			label.textContent = isDark ? "Modo oscuro activado" : "Modo oscuro";
		}
	};

	const initializeThemeSwitches = () => {
		document.querySelectorAll(".theme-switch-input").forEach((input) => {
			updateSwitch(input);
			input.addEventListener("change", () => {
				root.dataset.bsTheme = input.checked ? "dark" : "light";
				document.querySelectorAll(".theme-switch-input").forEach(updateSwitch);
				try {
					localStorage.setItem("cintac-theme", root.dataset.bsTheme);
				} catch (error) {
					console.warn("No se pudo guardar la preferencia del tema.", error);
				}
			});
		});
	};

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", initializeThemeSwitches, { once: true });
	} else {
		initializeThemeSwitches();
	}
})();
