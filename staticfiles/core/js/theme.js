(function () {
    "use strict";

    /*
     * =========================================================
     * KOUNTER GLOBAL THEME SYSTEM
     * =========================================================
     *
     * One theme state for the entire application.
     *
     * Canonical key:
     *     pos-theme
     *
     * Legacy key:
     *     kounter-theme
     *
     * Both are kept synchronized so older code cannot
     * accidentally reset the selected theme.
     */

    const STORAGE_KEY = "pos-theme";
    const LEGACY_KEY = "kounter-theme";


    /* =========================================================
       GET SAVED THEME
    ========================================================= */

    function getStoredTheme() {

        const saved =
            localStorage.getItem(STORAGE_KEY) ||
            localStorage.getItem(LEGACY_KEY);


        return saved === "dark"
            ? "dark"
            : "light";
    }



    /* =========================================================
       KEEP BOTH STORAGE KEYS SYNCHRONIZED
    ========================================================= */

    function syncStorage(theme) {

        localStorage.setItem(
            STORAGE_KEY,
            theme
        );


        localStorage.setItem(
            LEGACY_KEY,
            theme
        );

    }



    /* =========================================================
       UPDATE ALL THEME CONTROLS
    ========================================================= */

    function updateControls(theme) {

        const isDark =
            theme === "dark";


        document
            .querySelectorAll(
                ".theme-switch, .theme-toggle, [data-theme-toggle]"
            )
            .forEach(function (control) {


                /*
                 * Checkbox-style theme controls
                 */

                if (
                    control.tagName === "INPUT" &&
                    control.type === "checkbox"
                ) {

                    control.checked =
                        isDark;

                }


                /*
                 * Accessibility
                 */

                control.setAttribute(
                    "aria-pressed",
                    isDark
                        ? "true"
                        : "false"
                );


                control.setAttribute(
                    "aria-label",
                    isDark
                        ? "Switch to light mode"
                        : "Switch to dark mode"
                );


                control.setAttribute(
                    "title",
                    isDark
                        ? "Switch to light mode"
                        : "Switch to dark mode"
                );

            });

    }



    /* =========================================================
       APPLY THEME
    ========================================================= */

    function applyTheme(
        theme,
        save
    ) {

        const selectedTheme =
            theme === "dark"
                ? "dark"
                : "light";


        /*
         * This is the single source of truth.
         */

        document.documentElement.setAttribute(
            "data-theme",
            selectedTheme
        );


        /*
         * Save the selection.
         */

        if (save !== false) {

            syncStorage(
                selectedTheme
            );

        }


        /*
         * Update switches.
         */

        updateControls(
            selectedTheme
        );


        /*
         * Dashboard sales graph support.
         */

        if (
            typeof window.drawSalesGraph ===
            "function"
        ) {

            try {

                window.drawSalesGraph();

            } catch (error) {

                /*
                 * A graph error must never
                 * break the theme system.
                 */

            }

        }

    }



    /* =========================================================
       TOGGLE THEME
    ========================================================= */

    function toggleTheme() {

        const currentTheme =
            document.documentElement.getAttribute(
                "data-theme"
            ) ||
            getStoredTheme();


        const nextTheme =
            currentTheme === "dark"
                ? "light"
                : "dark";


        applyTheme(
            nextTheme
        );

    }



    /* =========================================================
       GLOBAL KOUNTER THEME API
    ========================================================= */

    window.KounterTheme = {

        set: function (theme) {

            applyTheme(
                theme
            );

        },


        toggle: function () {

            toggleTheme();

        },


        current: function () {

            return (
                document.documentElement.getAttribute(
                    "data-theme"
                ) ||
                getStoredTheme()
            );

        }

    };



    /* =========================================================
       APPLY SAVED THEME IMMEDIATELY
    =========================================================
    
    This happens before DOMContentLoaded.

    Therefore navigating from:

        Dashboard → POS
        POS → Sales
        Sales → Products

    will NOT reset the theme.
    */

    applyTheme(
        getStoredTheme(),
        false
    );



    /* =========================================================
       INITIALIZE THEME BUTTONS
    ========================================================= */

    function initializeThemeControls() {

        updateControls(
            document.documentElement.getAttribute(
                "data-theme"
            )
        );


        document
            .querySelectorAll(
                ".theme-switch, .theme-toggle"
            )
            .forEach(function (button) {


                /*
                 * Prevent duplicate listeners.
                 */

                if (
                    button.dataset
                        .kounterThemeBound ===
                    "true"
                ) {

                    return;

                }


                button.dataset.kounterThemeBound =
                    "true";


                button.addEventListener(
                    "click",
                    function (event) {

                        event.preventDefault();

                        toggleTheme();

                    }
                );

            });

    }



    /* =========================================================
       DOM READY
    ========================================================= */

    if (
        document.readyState ===
        "loading"
    ) {

        document.addEventListener(
            "DOMContentLoaded",
            initializeThemeControls
        );

    } else {

        initializeThemeControls();

    }



    /* =========================================================
       SYNCHRONIZE MULTIPLE BROWSER TABS
    ========================================================= */

    window.addEventListener(
        "storage",
        function (event) {


            if (
                event.key === STORAGE_KEY ||
                event.key === LEGACY_KEY
            ) {

                applyTheme(
                    event.newValue === "dark"
                        ? "dark"
                        : "light",

                    false
                );

            }

        }
    );

})();


(function () {
    "use strict";

    const THEME_KEY = "kounter-theme";

    function getSavedTheme() {
        return localStorage.getItem(THEME_KEY) || "light";
    }

    function applyTheme(theme) {
        theme = theme === "dark" ? "dark" : "light";

        document.documentElement.setAttribute("data-theme", theme);

        localStorage.setItem(THEME_KEY, theme);

        updateFavicon(theme);
    }

    function updateFavicon(theme) {
        const favicon = document.getElementById("kounter-favicon");

        if (!favicon) {
            return;
        }

        const lightIcon = favicon.dataset.light;
        const darkIcon = favicon.dataset.dark;

        favicon.href = theme === "dark"
            ? darkIcon
            : lightIcon;
    }

    function initializeTheme() {
        const savedTheme = getSavedTheme();

        document.documentElement.setAttribute(
            "data-theme",
            savedTheme
        );

        updateFavicon(savedTheme);
    }

    window.KounterTheme = {
        apply: applyTheme,
        get: getSavedTheme
    };

    initializeTheme();

    window.addEventListener("storage", function (event) {
        if (event.key === THEME_KEY) {
            const newTheme = event.newValue || "light";

            document.documentElement.setAttribute(
                "data-theme",
                newTheme
            );

            updateFavicon(newTheme);
        }
    });
})();