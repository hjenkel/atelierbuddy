from __future__ import annotations

from nicegui import ui


def apply_theme() -> None:
    ui.add_head_html(
        """
        <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\"> 
        <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>
        <link href=\"https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&family=Sora:wght@700;800&display=swap\" rel=\"stylesheet\">
        <script src=\"/assets/pdfjs/pdf.min.js\"></script>
        <script>
          (function () {
            const palette = {
              '--q-primary': '#5c30ff',
              '--q-secondary': '#f791c3',
              '--q-accent': '#f6bc3f',
              '--q-dark': '#1f160f',
              '--q-positive': '#008c47',
              '--q-negative': '#e63c23',
              '--q-info': '#5c30ff',
              '--q-warning': '#f6bc3f',
            };
            const applyPalette = () => {
              const targets = [document.documentElement, document.body].filter(Boolean);
              targets.forEach((el) => {
                Object.entries(palette).forEach(([key, value]) => el.style.setProperty(key, value));
              });
            };
            if (document.readyState === 'loading') {
              document.addEventListener('DOMContentLoaded', applyPalette, { once: true });
            } else {
              applyPalette();
            }
            window.addEventListener('load', applyPalette, { once: true });
            setTimeout(applyPalette, 120);
          })();
        </script>
        """,
        shared=True,
    )

    ui.add_css(
        """
        /* Design-Tokens: zentrale Farben, Schattierung und globale Maße */
        :root {
          --bm-bg-main: #ffe8ce;
          --bm-accent-primary: #5c30ff;
          --bm-accent-alert: #e63c23;
          --bm-accent-play-1: #f791c3;
          --bm-accent-play-2: #f6bc3f;
          --bm-accent-positive: #008c47;
          --bm-bg: var(--bm-bg-main);
          --bm-surface: #fffdf8;
          --bm-surface-strong: #ffffff;
          --bm-surface-alt: #fff2dc;
          --bm-text: #1f160f;
          --bm-muted: #4d3b2a;
          --bm-border: #1f160f;
          --bm-primary: var(--bm-accent-primary);
          --bm-primary-soft: var(--bm-primary);
          --bm-coral: var(--bm-accent-alert);
          --bm-yellow: var(--bm-accent-play-2);
          --bm-mint: var(--bm-accent-play-1);
          --bm-success: var(--bm-accent-positive);
          --bm-warning: var(--bm-accent-play-2);
          --bm-danger: var(--bm-accent-alert);
          --bm-shadow: 4px 4px 0 var(--bm-border);
          --bm-shadow-soft: 2px 2px 0 var(--bm-border);
          --bm-header-height: 46px;
          --bm-field-label-offset-x: 2px;
          --q-primary: var(--bm-primary);
          --q-secondary: var(--bm-accent-play-1);
          --q-accent: var(--bm-accent-play-2);
          --q-positive: var(--bm-success);
          --q-negative: var(--bm-danger);
          --q-warning: var(--bm-warning);
          --q-dark: var(--bm-text);
        }

        body {
          font-family: 'Manrope', sans-serif;
          color: var(--bm-text);
          background: var(--bm-bg-main);
          font-variant-numeric: tabular-nums;
        }

        .bm-page-title {
          font-family: 'Sora', sans-serif;
          font-weight: 800;
          font-size: clamp(2rem, 3.2vw, 3rem);
          color: var(--bm-text);
          text-transform: uppercase;
          letter-spacing: 0.03em;
          line-height: 0.95;
        }

        .bm-brand {
          font-family: 'Manrope', sans-serif;
          font-weight: 800;
          letter-spacing: 0.02em;
        }

        .bm-app-root {
          min-height: 100vh;
        }

        .bm-global-header {
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          z-index: 2100;
          background: var(--bm-accent-play-1);
          border-bottom: 3px solid var(--bm-border);
          box-shadow: var(--bm-shadow);
        }

        .bm-global-header-inner {
          height: var(--bm-header-height);
          padding: 6px 12px;
          box-sizing: border-box;
        }

        .bm-global-brand-badge {
          width: 32px;
          height: 32px;
          flex: 0 0 auto;
          display: flex;
          align-items: center;
          justify-content: center;
          background: var(--bm-surface-strong);
          border: 2px solid var(--bm-border);
          border-radius: 8px;
          box-shadow: var(--bm-shadow-soft);
          overflow: hidden;
        }

        .bm-global-brand-logo {
          width: 24px;
          height: 24px;
          object-fit: contain;
          image-rendering: pixelated;
          image-rendering: crisp-edges;
        }

        .bm-global-brand {
          font-family: 'Manrope', sans-serif;
          font-weight: 800;
          font-size: 0.98rem;
          letter-spacing: 0.02em;
        }

        .bm-global-icon-btn {
          color: var(--bm-text);
          border: 2px solid var(--bm-border);
          background: var(--bm-surface-strong);
          box-shadow: var(--bm-shadow-soft);
        }

        .bm-global-icon-btn:hover {
          background: var(--bm-accent-play-2);
          transform: translate(-1px, -1px);
          box-shadow: 3px 3px 0 var(--bm-border);
        }

        .bm-global-icon-btn:active {
          transform: translate(1px, 1px);
          box-shadow: 1px 1px 0 var(--bm-border);
        }

        .bm-help-menu {
          max-width: calc(100vw - 20px) !important;
          background: transparent !important;
          box-shadow: none !important;
          z-index: 4200 !important;
          overflow: visible !important;
        }

        .q-menu.bm-help-menu,
        body .q-menu.bm-help-menu,
        .q-position-engine .q-menu.bm-help-menu {
          z-index: 4200 !important;
          overflow: visible !important;
        }

        .q-menu.bm-help-menu .bm-help-panel {
          position: relative;
          z-index: 4201 !important;
        }

        .bm-help-panel {
          position: relative;
          min-width: 260px;
          width: min(420px, calc(100vw - 28px));
          max-width: calc(100vw - 20px);
          padding: 10px 12px;
          background: var(--bm-surface);
          border: 2px solid var(--bm-border);
          border-radius: 8px;
          box-shadow: var(--bm-shadow);
          overflow: visible;
          isolation: isolate;
        }

        .bm-help-panel::before {
          content: '';
          position: absolute;
          top: -8px;
          right: 10px;
          width: 14px;
          height: 14px;
          background: var(--bm-surface);
          border-left: 2px solid var(--bm-border);
          border-top: 2px solid var(--bm-border);
          transform: rotate(45deg);
          z-index: 3;
        }

        .bm-help-popover-title {
          font-weight: 800;
          font-size: 0.95rem;
          color: var(--bm-text);
        }

        .bm-help-popover-close {
          color: var(--bm-muted);
        }

        @media (max-width: 640px) {
          .bm-help-panel {
            width: calc(100vw - 18px);
            max-width: calc(100vw - 18px);
            padding: 9px 10px;
          }
          .bm-help-panel::before {
            right: 10px;
          }
        }

        .bm-help-popover-body {
          margin-top: 4px;
          line-height: 1.45;
          color: var(--bm-muted);
        }

        .bm-on-dark-title {
          color: var(--bm-text);
        }

        .bm-status-positive-text {
          color: var(--bm-success);
          font-weight: 700;
        }

        .bm-report-title {
          color: var(--bm-text) !important;
          text-shadow: none;
        }

        /* App-Grundlayout: Header-versetzter Inhalt + Sidebar/Content-Struktur */
        .bm-app-shell {
          margin-top: var(--bm-header-height);
          min-height: calc(100vh - var(--bm-header-height));
          align-items: stretch;
        }

        .bm-sidebar {
          width: 264px;
          padding: 14px;
          gap: 10px;
          border-right: 3px solid var(--bm-border);
          background: var(--bm-accent-play-2);
        }

        .bm-sidebar--mini {
          width: 84px;
        }

        .bm-sidebar-header {
          border-radius: 8px;
          background: transparent;
          border: 0;
          box-shadow: none;
          padding: 0;
        }

        .bm-sidebar-toggle-btn {
          min-height: 42px;
          width: 100%;
        }

        .bm-nav-item {
          position: relative;
          min-height: 42px;
          border-radius: 8px;
          color: var(--bm-text);
          font-weight: 700;
          text-transform: none;
          width: 100%;
          border: 2px solid transparent;
          box-shadow: none;
        }

        .bm-nav-item .q-btn__content,
        .bm-nav-item .q-icon {
          color: var(--bm-text);
        }

        .bm-nav-item .q-btn__content * {
          color: var(--bm-text) !important;
        }

        .bm-nav-item:not(.bm-nav-item--active),
        .bm-nav-item:not(.bm-nav-item--active) .q-btn__content,
        .bm-nav-item:not(.bm-nav-item--active) .q-btn__content *,
        .bm-nav-item:not(.bm-nav-item--active) .q-icon {
          color: var(--bm-text) !important;
        }

        .bm-nav-item:hover {
          background: var(--bm-accent-alert);
          color: #ffffff;
          border-color: var(--bm-border);
          transform: translate(-1px, -1px);
          box-shadow: var(--bm-shadow-soft);
        }

        .bm-nav-item:hover .q-btn__content,
        .bm-nav-item:hover .q-btn__content *,
        .bm-nav-item:hover .q-icon {
          color: #ffffff !important;
        }

        .bm-nav-item--active {
          background: var(--bm-primary);
          color: #ffffff;
          border: 2px solid var(--bm-border);
          box-shadow: var(--bm-shadow-soft);
        }

        .bm-nav-item--active .q-btn__content,
        .bm-nav-item--active .q-btn__content *,
        .bm-nav-item--active .q-icon {
          color: #ffffff !important;
        }

        .bm-nav-item--nested {
          margin-left: 18px;
          margin-top: 4px;
          padding-left: 8px;
          width: calc(100% - 18px);
          box-sizing: border-box;
        }

        .bm-nav-item--nested::before {
          content: '';
          position: absolute;
          left: -10px;
          top: -6px;
          bottom: -6px;
          width: 2px;
          border-radius: 999px;
          background: var(--bm-border);
          opacity: 0.35;
        }

        .bm-nav-item--mini {
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 0;
          min-height: 42px;
        }

        .bm-nav-item--mini .q-btn__content {
          width: 100%;
          justify-content: center;
          align-items: center;
        }

        .bm-nav-item--mini .q-icon {
          margin: 0;
        }

        .bm-nav-item--nested-mini {
          margin-left: 10px;
          width: calc(100% - 10px);
          margin-top: 4px;
          box-shadow: none;
        }

        .bm-nav-item--nested-mini::before {
          content: '';
          position: absolute;
          left: -8px;
          top: -8px;
          bottom: -8px;
          width: 2px;
          border-radius: 999px;
          background: var(--bm-border);
          opacity: 0.45;
        }

        .bm-nav-group-trigger {
          margin-bottom: 2px;
        }

        .bm-nav-group-trigger--open {
          border-color: var(--bm-border);
          background: var(--bm-surface-strong);
          box-shadow: var(--bm-shadow-soft);
        }

        .bm-nav-group-trigger--open.bm-nav-item--active,
        .bm-nav-group-trigger--open.bm-nav-item--active .q-btn__content,
        .bm-nav-group-trigger--open.bm-nav-item--active .q-btn__content *,
        .bm-nav-group-trigger--open.bm-nav-item--active .q-icon {
          color: var(--bm-text) !important;
          background: var(--bm-surface-strong) !important;
        }

        .bm-content {
          flex: 1;
          min-width: 0;
          min-height: calc(100vh - var(--bm-header-height));
          padding: 16px;
          background: var(--bm-bg-main);
        }

        .nicegui-content {
          padding: 0 !important;
        }

        .bm-context-dashboard,
        .bm-context-expenses,
        .bm-context-works,
        .bm-context-reports,
        .bm-context-settings {
          background: var(--bm-bg-main);
        }

        .bm-context-dashboard .bm-content,
        .bm-context-expenses .bm-content,
        .bm-context-works .bm-content,
        .bm-context-reports .bm-content,
        .bm-context-settings .bm-content {
          background: var(--bm-bg-main);
        }

        .bm-page-head {
          position: relative;
          overflow: hidden;
          margin-bottom: 10px;
          border-radius: 8px;
          padding: 12px 14px;
          background: var(--bm-surface-strong);
          border: 2px solid var(--bm-border);
          box-shadow: var(--bm-shadow);
          max-width: 100%;
        }

        .bm-page-head::before {
          content: '';
          position: absolute;
          left: 0;
          top: 0;
          width: 10px;
          height: 100%;
          border-radius: 0;
          background: var(--bm-accent-primary);
          border: none;
        }

        .bm-card {
          background: var(--bm-surface);
          border-radius: 8px;
          border: 2px solid var(--bm-border);
          box-shadow: var(--bm-shadow);
        }

        /* Quasar-Field-Overrides: !important ist nötig, um Framework-Defaults zuverlässig zu übersteuern. */
        .q-field__control {
          border: 2px solid var(--bm-border) !important;
          border-radius: 8px !important;
          background: var(--bm-surface-strong) !important;
          box-shadow: none !important;
          min-height: 44px !important;
          padding: 0 12px !important;
        }

        /* Label bündig zum Eingabetext mit kleinem optischen Offset */
        .q-field__label {
          left: 0 !important;
          padding-left: var(--bm-field-label-offset-x) !important;
          padding-right: 0 !important;
        }

        /* Quasar setzt für Floating-Labels separate Positionsregeln */
        .q-field--float .q-field__label {
          left: 0 !important;
        }

        .q-field__native,
        .q-field__input {
          padding-left: 0 !important;
          padding-right: 0 !important;
        }

        .q-field__control::before,
        .q-field__control::after,
        .q-field__bottom::before,
        .q-field__bottom::after {
          display: none !important;
          border: 0 !important;
          content: none !important;
        }

        .q-field__control,
        .q-field__label,
        .q-field__native,
        .q-field__input,
        .q-field__marginal {
          transition: none !important;
        }

        .q-field--focused .q-field__control {
          box-shadow: 3px 3px 0 var(--bm-border) !important;
          border-color: var(--bm-primary) !important;
        }

        .q-field--readonly .q-field__control,
        .q-field--disabled .q-field__control {
          background: var(--bm-bg-main) !important;
          border-color: var(--bm-muted) !important;
          box-shadow: none !important;
        }

        .q-field--readonly .q-field__native,
        .q-field--readonly .q-field__input,
        .q-field--readonly .q-field__label {
          color: var(--bm-muted) !important;
        }

        /* Basisstil für Quasar-Buttons */
        .q-btn {
          border-radius: 8px;
          border: 2px solid var(--bm-border);
          box-shadow: var(--bm-shadow-soft);
          font-weight: 700;
          letter-spacing: 0.01em;
        }

        .q-btn:hover {
          transform: translate(-1px, -1px);
          box-shadow: 3px 3px 0 var(--bm-border);
        }

        .q-btn:active {
          transform: translate(1px, 1px);
          box-shadow: 1px 1px 0 var(--bm-border);
        }

        .q-btn.bg-primary {
          background: var(--bm-primary) !important;
          color: #fff !important;
        }

        .q-btn.bg-negative {
          background: var(--bm-danger) !important;
          color: #fff !important;
        }

        .q-btn.bg-positive {
          background: var(--bm-success) !important;
          color: #fff !important;
        }

        .q-btn.bg-warning {
          background: var(--bm-warning) !important;
          color: var(--bm-text) !important;
        }

        .bm-view-mode-btn {
          min-width: 166px;
          min-height: 44px !important;
        }

        .bm-toolbar-btn {
          min-height: 44px !important;
          min-width: 166px;
        }

        .bm-segment-btn {
          transition: none;
        }

        .bm-segment-btn--active {
          background: var(--bm-primary) !important;
          color: #fff !important;
          border-color: var(--bm-border) !important;
          box-shadow: var(--bm-shadow-soft) !important;
        }

        .bm-segment-btn--inactive {
          background: var(--bm-surface-strong) !important;
          color: var(--bm-text) !important;
          border-color: var(--bm-border) !important;
          box-shadow: none !important;
        }

        .bm-clean-toggle .q-btn-group {
          border: 2px solid var(--bm-border);
          border-radius: 12px;
          overflow: hidden;
          background: var(--bm-surface-alt);
          box-shadow: none;
        }

        .bm-clean-toggle .q-btn {
          border-radius: 0;
          box-shadow: none;
          min-height: 38px;
        }

        .bm-inline-switch {
          padding: 2px 8px;
          border-radius: 999px;
          background: var(--bm-surface-strong);
          border: 2px solid var(--bm-border);
        }

        .bm-doc-type-toggle.q-btn-group,
        .bm-doc-type-toggle .q-btn-group {
          display: inline-flex !important;
          align-items: center;
          border: 0 !important;
          background: transparent !important;
          box-shadow: none !important;
          padding: 0 !important;
          gap: 0 !important;
        }

        .bm-doc-type-toggle .q-btn {
          min-height: 42px !important;
        }

        .bm-doc-type-toggle .q-btn + .q-btn {
          margin-left: 14px !important;
        }

        .bm-detail-card {
          height: calc(100dvh - var(--bm-header-height) - 170px);
          max-height: calc(100dvh - var(--bm-header-height) - 170px);
          overflow: hidden;
          display: flex;
          flex-direction: column;
          gap: 12px;
        }

        .bm-detail-toolbar {
          flex: 0 0 auto;
        }

        .bm-icon-action-btn {
          min-height: 40px !important;
          min-width: 40px !important;
          border: 2px solid var(--bm-border) !important;
          background: var(--bm-surface-strong) !important;
          color: var(--bm-text) !important;
          box-shadow: var(--bm-shadow-soft) !important;
        }

        .bm-icon-action-btn--primary {
          background: var(--bm-primary) !important;
          color: #fff !important;
        }

        .q-btn.bm-icon-action-btn--primary,
        .q-btn.bm-icon-action-btn--primary .q-icon,
        .q-btn.bm-icon-action-btn--primary .q-btn__content,
        .q-btn.bm-icon-action-btn--primary .q-btn__content * {
          color: #fff !important;
        }

        .bm-icon-action-btn--danger {
          background: var(--bm-danger) !important;
          color: #fff !important;
        }

        .q-btn.bm-icon-action-btn--danger,
        .q-btn.bm-icon-action-btn--danger .q-icon,
        .q-btn.bm-icon-action-btn--danger .q-btn__content,
        .q-btn.bm-icon-action-btn--danger .q-btn__content * {
          color: #fff !important;
        }

        .bm-icon-action-btn--success {
          background: var(--bm-success) !important;
          color: #fff !important;
        }

        .q-btn.bm-icon-action-btn--success,
        .q-btn.bm-icon-action-btn--success .q-icon,
        .q-btn.bm-icon-action-btn--success .q-btn__content,
        .q-btn.bm-icon-action-btn--success .q-btn__content * {
          color: #fff !important;
        }

        .bm-table .q-table thead tr th {
          font-weight: 700;
          color: var(--bm-text);
          background: var(--bm-accent-play-2);
          border-bottom: 2px solid var(--bm-border);
        }

        .bm-table .q-table tbody tr:nth-child(even) {
          background: #fff9f0;
        }

        .bm-table .q-table tbody tr:hover {
          background: #ffeeca;
        }

        .q-badge {
          border: 2px solid var(--bm-border);
          border-radius: 999px;
          font-weight: 700;
          letter-spacing: 0.01em;
        }

        .q-badge.q-badge--outline {
          background: var(--bm-surface-strong) !important;
        }

        .bm-filter-row {
          align-items: end;
          gap: 12px;
        }

        .bm-filter-btn {
          min-height: 44px !important;
        }

        .bm-neutral-action-btn {
          min-height: 44px !important;
          background: var(--bm-surface-strong) !important;
          color: var(--bm-text) !important;
          border: 2px solid var(--bm-border) !important;
          box-shadow: var(--bm-shadow-soft) !important;
        }

        .bm-licenses-dialog {
          width: min(1200px, 96vw);
          max-width: 96vw;
          max-height: 88vh;
          display: flex !important;
          flex-direction: column !important;
          gap: 12px;
          overflow: hidden;
          box-sizing: border-box;
        }

        .bm-licenses-content {
          width: 100%;
          min-height: 0;
          flex: 1 1 auto;
          display: flex !important;
          flex-direction: column !important;
          gap: 12px;
          overflow-y: auto;
          overflow-x: hidden;
          box-sizing: border-box;
        }

        .bm-licenses-table {
          width: 100%;
          min-width: 0;
        }

        .bm-licenses-table .q-table__middle {
          overflow-x: hidden !important;
        }

        .bm-licenses-table table {
          width: 100%;
          table-layout: fixed;
        }

        .bm-licenses-table td,
        .bm-licenses-table th {
          white-space: normal !important;
          word-break: break-word;
          overflow-wrap: anywhere;
        }

        .bm-content .bm-card,
        .bm-content .q-table,
        .bm-content .q-field__control,
        .bm-content .q-dialog__inner > div {
          background: var(--bm-surface);
        }

        .bm-upload-zone {
          border: 2px dashed var(--bm-primary);
          border-radius: 8px;
          background: var(--bm-surface);
        }

        .bm-upload-zone:hover {
          border-color: var(--bm-coral);
          background: #fff;
        }

        /* Viewer-Komponenten (PDF/Bild) teilen denselben Rahmen- und Toolbar-Stil */
        .bm-pdf-viewer {
          display: flex;
          flex-direction: column;
          width: 100%;
          max-width: 100%;
          min-width: 0;
          height: 100%;
          background: #ffffff;
          border: 2px solid var(--bm-border);
          border-radius: 8px;
          overflow: hidden;
          box-sizing: border-box;
        }

        .bm-pdf-toolbar {
          flex: 0 0 auto;
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 10px;
          border-bottom: 2px solid var(--bm-border);
          background: var(--bm-surface-alt);
        }

        .bm-pdf-toolbar button {
          min-width: 34px;
          height: 32px;
          border: 2px solid var(--bm-border);
          border-radius: 8px;
          background: var(--bm-surface);
          color: var(--bm-text);
          font-weight: 700;
          cursor: pointer;
        }

        .bm-pdf-toolbar button:hover {
          background: var(--bm-primary);
          color: #fff;
        }

        .bm-pdf-toolbar button:disabled {
          opacity: 0.45;
          cursor: not-allowed;
        }

        .bm-pdf-label {
          font-size: 0.82rem;
          font-weight: 700;
          color: var(--bm-muted);
          min-width: 84px;
          text-align: center;
        }

        .bm-pdf-spacer {
          flex: 1;
        }

        .bm-pdf-viewport {
          flex: 1 1 auto;
          width: 100%;
          max-width: 100%;
          min-width: 0;
          min-height: 0;
          height: auto;
          overflow: auto;
          background: var(--bm-bg-main);
          padding: 12px;
          display: block;
          box-sizing: border-box;
        }

        .bm-pdf-viewport canvas {
          display: block;
          width: auto;
          height: auto;
          max-width: none;
          margin: 0 auto;
          flex: 0 0 auto;
          box-shadow: 0 4px 20px rgba(20, 32, 55, 0.2);
          background: #fff;
        }

        .bm-pdf-status {
          flex: 0 0 auto;
          font-size: 0.82rem;
          color: var(--bm-muted);
          padding: 8px 10px;
          border-top: 2px solid var(--bm-border);
          background: var(--bm-surface);
        }

        .bm-detail-grid {
          display: grid;
          grid-template-columns: minmax(0, 1.5fr) minmax(320px, 1fr);
          gap: 16px;
          align-items: start;
          height: 100%;
          min-height: 0;
        }

        .bm-detail-preview, .bm-detail-form {
          min-width: 0;
          width: 100%;
          min-height: 0;
        }

        .bm-detail-preview {
          overflow: hidden;
          display: flex;
          flex-direction: column;
          height: 100%;
        }

        .bm-detail-preview-frame {
          flex: 1 1 auto;
          min-height: 0;
        }

        .bm-image-viewer {
          display: flex;
          flex-direction: column;
          width: 100%;
          max-width: 100%;
          min-width: 0;
          height: 100%;
          background: #ffffff;
          border: 2px solid var(--bm-border);
          border-radius: 8px;
          overflow: hidden;
          box-sizing: border-box;
        }

        .bm-image-toolbar {
          flex: 0 0 auto;
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 10px;
          border-bottom: 2px solid var(--bm-border);
          background: var(--bm-surface-alt);
        }

        .bm-image-toolbar button {
          min-width: 34px;
          height: 32px;
          border: 2px solid var(--bm-border);
          border-radius: 8px;
          background: var(--bm-surface);
          color: var(--bm-text);
          font-weight: 700;
          cursor: pointer;
        }

        .bm-image-toolbar button:hover {
          background: var(--bm-primary);
          color: #fff;
        }

        .bm-image-toolbar button:disabled {
          opacity: 0.45;
          cursor: not-allowed;
        }

        .bm-image-label {
          font-size: 0.82rem;
          font-weight: 700;
          color: var(--bm-muted);
          min-width: 84px;
          text-align: center;
        }

        .bm-image-spacer {
          flex: 1;
        }

        .bm-image-viewport {
          flex: 1 1 auto;
          width: 100%;
          max-width: 100%;
          min-width: 0;
          min-height: 0;
          height: auto;
          overflow: auto;
          background: var(--bm-bg-main);
          padding: 12px;
          display: block;
          box-sizing: border-box;
        }

        .bm-image-stage {
          display: block;
          width: auto;
          height: auto;
          max-width: none;
          margin: 0 auto;
          transform-origin: top left;
          box-shadow: 0 4px 20px rgba(20, 32, 55, 0.2);
          background: #fff;
        }

        .bm-image-status {
          flex: 0 0 auto;
          font-size: 0.82rem;
          color: var(--bm-muted);
          padding: 8px 10px;
          border-top: 2px solid var(--bm-border);
          background: var(--bm-surface);
        }

        .bm-detail-form {
          overflow-y: auto;
          max-height: 100%;
          padding-right: 4px;
        }

        .q-btn.bm-inline-create-btn {
          height: 56px !important;
          min-height: 56px !important;
          max-height: 56px !important;
          width: 56px !important;
          min-width: 56px !important;
          max-width: 56px !important;
          padding: 0 !important;
          border-radius: 8px !important;
          border: 2px solid var(--bm-border) !important;
          background: var(--bm-surface-strong) !important;
          color: var(--bm-text) !important;
          box-shadow: var(--bm-shadow-soft) !important;
          align-self: flex-end;
        }

        .q-btn.bm-inline-create-btn .q-btn__content {
          min-height: 52px !important;
          line-height: 1 !important;
        }

        .bm-allocation-line .q-field {
          margin-bottom: 0 !important;
          min-height: 56px !important;
          height: 56px !important;
        }

        .bm-allocation-main-field.q-field,
        .bm-allocation-side-field.q-field {
          min-height: 56px !important;
          height: 56px !important;
        }

        .bm-allocation-main-field .q-field__control,
        .bm-allocation-side-field .q-field__control,
        .bm-allocation-line .q-field__control {
          min-height: 56px !important;
          height: 56px !important;
        }

        .bm-allocation-line > * {
          align-self: flex-end;
        }

        .bm-allocation-line .q-btn.bm-inline-create-btn {
          height: 56px !important;
          min-height: 56px !important;
          max-height: 56px !important;
        }

        .bm-allocation-line {
          width: 100%;
          align-items: flex-end;
          gap: 8px;
          flex-wrap: wrap;
        }

        .bm-allocation-main-field {
          min-width: 0;
          flex: 1 1 220px;
        }

        .bm-allocation-side-field {
          width: 140px;
          min-width: 140px;
        }

        .bm-amount-expense {
          color: var(--bm-danger);
          font-weight: 800;
        }

        .bm-amount-income {
          color: var(--bm-success);
          font-weight: 800;
        }

        .bm-amount-neutral {
          color: var(--bm-text);
          font-weight: 700;
        }

        .bm-stat-card {
          border-width: 2px;
        }

        .bm-stat-label {
          color: var(--bm-muted);
          font-weight: 700;
        }

        .bm-dashboard-thumb-card {
          width: 172px;
          min-height: 214px;
          padding: 8px;
          gap: 8px;
          display: flex;
          flex-direction: column;
        }

        .bm-dashboard-thumb-media {
          width: 100%;
          height: 138px;
          border: 2px solid var(--bm-border);
          border-radius: 8px;
          overflow: hidden;
          background: #fff;
          display: flex;
          align-items: center;
          justify-content: center;
        }

        .bm-dashboard-thumb-meta {
          display: flex;
          flex-direction: column;
          gap: 2px;
          padding: 0 2px;
        }

        .bm-dashboard-thumb-caption {
          font-size: 0.72rem;
          font-weight: 700;
          color: var(--bm-muted);
          line-height: 1.1;
        }

        .bm-dashboard-thumb-date {
          font-size: 0.85rem;
          font-weight: 800;
          color: var(--bm-text);
          line-height: 1.2;
        }

        /* Responsive Detailansicht: ab Tablet in eine Spalte umbrechen */
        @media (max-width: 1180px) {
          .bm-detail-card {
            max-height: none;
            overflow: visible;
          }
          .bm-detail-grid {
            grid-template-columns: 1fr;
            height: auto;
          }
          .bm-detail-form {
            max-height: none;
            overflow: visible;
          }
        }
        """,
        shared=True,
    )
