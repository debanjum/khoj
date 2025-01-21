;;; khoj.el --- Your Second Brain -*- lexical-binding: t -*-

;; Copyright (C) 2021-2023 Khoj Inc.

;; Author: Debanjum Singh Solanky <debanjum@khoj.dev>
;;         Saba Imran <saba@khoj.dev>
;; Description: Your Second Brain
;; Keywords: search, chat, ai, org-mode, outlines, markdown, pdf, image
;; Version: 1.34.0
;; Package-Requires: ((emacs "27.1") (transient "0.3.0") (dash "2.19.1"))
;; URL: https://github.com/khoj-ai/khoj/tree/master/src/interface/emacs

;; This file is NOT part of GNU Emacs.

;;; License:

;; This program is free software; you can redistribute it and/or
;; modify it under the terms of the GNU General Public License
;; as published by the Free Software Foundation; either version 3
;; of the License, or (at your option) any later version.

;; This program is distributed in the hope that it will be useful,
;; but WITHOUT ANY WARRANTY; without even the implied warranty of
;; MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
;; GNU General Public License for more details.

;; You should have received a copy of the GNU General Public License
;; along with this program. If not, see <http://www.gnu.org/licenses/>.

;;; Commentary:

;; Bootstrap your Second Brain from your `org-mode', `markdown' notes,
;; PDFs and images. Khoj exposes 2 modes, search and chat:
;;
;; Chat provides faster answers, iterative discovery and assisted
;; creativity.
;;
;; Search allows natural language, incremental search.
;;
;; Quickstart
;; -------------
;; 1. Install khoj.el from MELPA Stable
;;    (use-package khoj :pin melpa-stable :bind ("C-c s" . 'khoj))
;; 2. Set API key from https://app.khoj.dev/settings#clients (if not self-hosting)
;;    (setq khoj-api-key "YOUR_KHOJ_API_KEY")
;; 2. Start khoj from Emacs
;;    C-c s or M-x khoj
;;
;; See the repository docs for detailed setup and configuration steps.

;;; Code:

(require 'url)
(require 'json)
(require 'transient)
(require 'outline)
(require 'dash)
(require 'org)

(eval-when-compile (require 'subr-x)) ;; for string-trim before Emacs 28.2


;; -------------------------
;; Khoj Static Configuration
;; -------------------------

(defcustom khoj-server-url "https://app.khoj.dev"
  "Location of Khoj API server."
  :group 'khoj
  :type 'string)

(defcustom khoj-server-is-local t
  "Is Khoj server on local machine?."
  :group 'khoj
  :type 'boolean)

(defcustom khoj-image-width 156
  "Width of rendered images returned by Khoj."
  :group 'khoj
  :type 'integer)

(defcustom khoj-image-height 156
  "Height of rendered images returned by Khoj."
  :group 'khoj
  :type 'integer)

(defcustom khoj-results-count 8
  "Number of results to show in search and use for chat responses."
  :group 'khoj
  :type 'integer)

(defcustom khoj-search-on-idle-time 0.3
  "Idle time (in seconds) to wait before triggering search."
  :group 'khoj
  :type 'number)

(defcustom khoj-auto-find-similar t
  "Should try find similar notes automatically."
  :group 'khoj
  :type 'boolean)

(defcustom khoj-api-key nil
  "API Key to your Khoj. Default at https://app.khoj.dev/settings#clients."
  :group 'khoj
  :type 'string)

(defcustom khoj-auto-index t
  "Should content be automatically re-indexed every `khoj-index-interval' seconds."
  :group 'khoj
  :type 'boolean)

(defcustom khoj-index-interval 3600
  "Interval (in seconds) to wait before updating content index."
  :group 'khoj
  :type 'number)

(defcustom khoj-index-files-batch 30
  "Number of files to send for indexing in each request."
  :group 'khoj
  :type 'number)

(defcustom khoj-default-content-type "all"
  "The default content type to perform search on."
  :group 'khoj
  :type '(choice (const "org")
                 (const "markdown")
                 (const "image")
                 (const "pdf")))

(defcustom khoj-default-agent "khoj"
  "The default agent to chat with. See https://app.khoj.dev/agents for available options."
  :group 'khoj
  :type 'string)


;; --------------------------
;; Khoj Dynamic Configuration
;; --------------------------

(defvar khoj--minibuffer-window nil
  "Minibuffer window used to enter query.")

(defconst khoj--query-prompt "🏮 Khoj: "
  "Query prompt shown in the minibuffer.")

(defconst khoj--search-buffer-name "*🏮 Khoj Search*"
  "Name of buffer to show search results from Khoj.")

(defconst khoj--chat-buffer-name "*🏮 Khoj Chat*"
  "Name of chat buffer for Khoj.")

(defvar khoj--selected-agent khoj-default-agent
  "Currently selected Khoj agent.")

(defvar khoj--content-type "org"
  "The type of content to perform search on.")

(defvar khoj--search-on-idle-timer nil
  "Idle timer to trigger incremental search.")

(defvar khoj--index-timer nil
  "Timer to trigger content indexing.")

(defvar khoj--indexed-files '()
  "Files that were indexed in previous content indexing run.")

(declare-function org-element-property "org-mode" (PROPERTY ELEMENT))
(declare-function org-element-type "org-mode" (ELEMENT))
(declare-function markdown-mode "markdown-mode" ())
(declare-function which-key--show-keymap "which-key" (KEYMAP-NAME KEYMAP &optional PRIOR-ARGS ALL
NO-PAGING FILTER))

(defun khoj--keybindings-info-message ()
  "Show available khoj keybindings in-context, when khoj invoked."
  (concat
   "
     Set Content Type
-------------------------\n"
   "C-c RET | improve sort \n"))

(defvar khoj--rerank nil "Track when re-rank of results triggered.")
(defvar khoj--reference-count 0 "Track number of references currently in chat bufffer.")
(defun khoj--improve-sort () "Use cross-encoder to improve sorting of search results." (interactive) (khoj--incremental-search t))
(defun khoj--make-search-keymap (&optional existing-keymap)
  "Setup keymap to configure Khoj search. Build of EXISTING-KEYMAP when passed."
  (let ((kmap (or existing-keymap (make-sparse-keymap))))
    (define-key kmap (kbd "C-c RET") #'khoj--improve-sort)
    kmap))

(defvar khoj--keymap nil "Track Khoj keymap in this variable.")
(defun khoj--display-keybinding-info ()
  "Display information on keybindings to customize khoj search.
Use `which-key` if available, else display simple message in echo area"
  (if (fboundp 'which-key-show-full-keymap)
      (let ((khoj--keymap (khoj--make-search-keymap)))
        (which-key--show-keymap (symbol-name 'khoj--keymap)
                                (symbol-value 'khoj--keymap)
                                nil t t))
    (message "%s" (khoj--keybindings-info-message))))

(defvar khoj--last-heading-pos nil
  "The last heading position point was in.")


;; ----------------
;; Khoj Setup
;; ----------------
(defcustom khoj-server-command
  (or (executable-find "khoj")
      (executable-find "khoj.exe")
      "khoj")
  "Command to interact with Khoj server."
  :type 'string
  :group 'khoj)

(defcustom khoj-server-args '()
  "Arguments to pass to Khoj server on startup."
  :type '(repeat string)
  :group 'khoj)

(defcustom khoj-server-python-command
  (if (equal system-type 'windows-nt)
      (or (executable-find "py")
          (executable-find "pythonw")
          "python")
    (if (executable-find "python")
        "python"
      ;; Fallback on systems where python is not
      ;; symlinked to python3.
      "python3"))
  "The Python interpreter used for the Khoj server.

Khoj will try to use the system interpreter if it exists. If you wish
to use a specific python interpreter (from a virtual environment
for example), set this to the full interpreter path."
  :type '(choice (const :tag "python" "python")
                 (const :tag "python3" "python3")
                 (const :tag "pythonw (Python on Windows)" "pythonw")
                 (const :tag "py (other Python on Windows)" "py")
                 (string :tag "Other"))
  :safe (lambda (val)
          (member val '("python" "python3" "pythonw" "py")))
  :group 'khoj)

(defcustom khoj-org-files nil
  "List of org-files to index on khoj server."
  :type '(repeat string)
  :group 'khoj)

(defcustom khoj-org-directories nil
  "List of directories with `org-mode' files to index on khoj server."
  :type '(repeat string)
  :group 'khoj)

(make-obsolete-variable 'khoj-org-directories 'khoj-index-directories "1.2.0" 'set)
(make-obsolete-variable 'khoj-org-files 'khoj-index-files "1.2.0" 'set)

(defcustom khoj-index-files (org-agenda-files t t)
  "List of org, md, text, pdf to index on khoj server."
  :type '(repeat string)
  :group 'khoj)

(defcustom khoj-index-directories nil
  "List of directories with org, md, text, pdf to index on khoj server."
  :type '(repeat string)
  :group 'khoj)

(defcustom khoj-auto-setup t
  "Automate install, configure and start of khoj server.
Auto invokes setup steps on calling main entrypoint."
  :type 'string
  :group 'khoj)

(defvar khoj--server-process nil "Track khoj server process.")
(defvar khoj--server-name "*khoj-server*" "Track khoj server buffer.")
(defvar khoj--server-ready? nil "Track if khoj server is ready to receive API calls.")
(defvar khoj--server-configured? t "Track if khoj server is configured to receive API calls.")
(defvar khoj--progressbar '(🌑 🌘 🌗 🌖 🌕 🌔 🌓 🌒) "Track progress via moon phase animations.")

(defun khoj--server-get-version ()
  "Return the khoj server version."
  (with-temp-buffer
    (call-process khoj-server-command nil t nil "--version")
    (goto-char (point-min))
    (re-search-forward "\\([a-z0-9.]+\\)")
    (match-string 1)))

(defun khoj--server-install-upgrade ()
  "Install or upgrade the khoj server."
  (with-temp-buffer
    (message "khoj.el: Installing server...")
    (if (/= (apply #'call-process khoj-server-python-command
                   nil t nil
                   "-m" "pip" "install" "--upgrade"
                   '("khoj"))
            0)
        (message "khoj.el: Failed to install Khoj server. Please install it manually using pip install `khoj'.\n%s" (buffer-string))
      (message "khoj.el: Installed and upgraded Khoj server version: %s" (khoj--server-get-version)))))

(defun khoj--server-start ()
  "Start the khoj server."
  (interactive)
  (let* ((url-parts (split-string (cadr (split-string khoj-server-url "://")) ":"))
         (server-host (nth 0 url-parts))
         (server-port (or (nth 1 url-parts) "80"))
         (server-args (append khoj-server-args
                              (list (format "--host=%s" server-host)
                                    (format "--port=%s" server-port)))))
    (message "khoj.el: Starting server at %s %s..." server-host server-port)
    (setq khoj--server-process
          (make-process
           :name khoj--server-name
           :buffer khoj--server-name
           :command (append (list khoj-server-command) server-args)
           :sentinel (lambda (_ event)
                       (message "khoj.el: khoj server stopped with: %s" event)
                       (setq khoj--server-ready? nil))
           :filter (lambda (process msg)
                     (cond ((string-match (format "Uvicorn running on %s" khoj-server-url) msg)
                            (progn
                              (setq khoj--server-ready? t)))
                           ((string-match "Batches:  " msg)
                            (when (string-match "\\([0-9]+\\.[0-9]+\\|\\([0-9]+\\)\\)%?" msg)
                              (message "khoj.el: %s updating index %s"
                                       (nth (% (string-to-number (match-string 1 msg)) (length khoj--progressbar)) khoj--progressbar)
                                       (match-string 0 msg)))
                            (setq khoj--server-configured? nil))
                           ((and (not khoj--server-configured?)
                                 (string-match "Processor reconfigured via API" msg))
                            (setq khoj--server-configured? t))
                           ((and (not khoj--server-ready?)
                                 (or (string-match "configure.py" msg)
                                     (string-match "main.py" msg)
                                     (string-match "api.py" msg)))
                            (dolist (line (split-string msg "\n"))
                              (when (string-match "  " line)
                                (message "khoj.el: %s" (nth 1 (split-string line "  " t " *")))))))
                     ;; call default process filter to write output to process buffer
                     (internal-default-process-filter process msg))))
    (set-process-query-on-exit-flag khoj--server-process nil)
    (when (not khoj--server-process)
        (message "khoj.el: Failed to start Khoj server. Please start it manually by running `khoj' on terminal.\n%s" (buffer-string)))))

(defun khoj--server-started? ()
  "Check if the khoj server has been started."
  ;; check for when server process handled from within emacs
  (if (and khoj--server-process
           (process-live-p khoj--server-process))
      t
    ;; else general check via ping to khoj-server-url
    (if (ignore-errors
          (url-retrieve-synchronously (format "%s/api/health" khoj-server-url)))
        ;; Successful ping to non-emacs khoj server indicates it is started and ready.
        ;; So update ready state tracker variable (and implicitly return true for started)
        (setq khoj--server-ready? t)
      nil)))

(defun khoj--server-restart ()
  "Restart the khoj server."
  (interactive)
  (khoj--server-stop)
  (khoj--server-start))

(defun khoj--server-stop ()
  "Stop the khoj server."
  (interactive)
  (when (khoj--server-started?)
    (message "khoj.el: Stopping server...")
    (kill-process khoj--server-process)
    (message "khoj.el: Stopped server.")))

(defun khoj--server-setup ()
  "Install and start the khoj server, if required."
  (interactive)
  ;; Install khoj server, if not available but expected on local machine
  (when (and khoj-server-is-local
             (or (not (executable-find khoj-server-command))
                 (not (khoj--server-get-version))))
      (khoj--server-install-upgrade))
  ;; Start khoj server if not already started
  (when (not (khoj--server-started?))
    (khoj--server-start)))

(defun khoj-setup (&optional interact)
  "Install and start Khoj server. Get permission if INTERACT is non-nil."
  (interactive "p")
  ;; Setup khoj server if not running
  (let* ((not-started (not (khoj--server-started?)))
         (permitted (if (and not-started interact)
                        (y-or-n-p "Could not connect to Khoj server. Should I install, start it for you?")
                      t)))
    ;; If user permits setup of khoj server from khoj.el
    (when permitted
      ; Install, start server if server not running
      (when not-started
        (khoj--server-setup))

      ;; Wait until server is ready
      ;; As server can be started but not ready to use
      (while (not khoj--server-ready?)
        (sit-for 0.5)))))


;; -------------------
;; Khoj Index Content
;; -------------------

(defun khoj--server-index-files (&optional force content-type file-paths)
  "Send files at `FILE-PATHS' to the Khoj server to index for search and chat.
`FORCE' re-indexes all files of `CONTENT-TYPE' even if they are already indexed."
  (interactive)
  (let* ((boundary (format "-------------------------%d" (random (expt 10 10))))
         ;; Use `khoj-index-directories', `khoj-index-files' when set, else fallback to `khoj-org-directories', `khoj-org-files'
         ;; This is a temporary change. `khoj-org-directories', `khoj-org-files' are deprecated. They will be removed in a future release
         (content-directories (or khoj-index-directories khoj-org-directories))
         (content-files (or khoj-index-files khoj-org-files))
         (files-to-index (mapcar
                          #'expand-file-name
                          (or file-paths
                              (append (mapcan (lambda (dir) (directory-files-recursively dir "\\.\\(org\\|md\\|markdown\\|pdf\\|txt\\|rst\\|xml\\|htm\\|html\\)$")) content-directories) content-files))))
         (type-query (if (or (equal content-type "all") (not content-type)) "" (format "t=%s" content-type)))
         (delete-files (-difference khoj--indexed-files files-to-index))
         (inhibit-message t)
         (message-log-max nil)
         (batch-size khoj-index-files-batch))
    (dolist (files (-partition-all batch-size files-to-index))
      (khoj--send-index-update-request (khoj--render-update-files-as-request-body files boundary) boundary content-type type-query force))
    (when delete-files
        (khoj--send-index-update-request (khoj--render-delete-files-as-request-body delete-files boundary) boundary content-type type-query force))
    (setq khoj--indexed-files files-to-index)))

(defun khoj--send-index-update-request (body boundary &optional content-type type-query force)
  "Send multi-part form `BODY' of `CONTENT-TYPE' in request to khoj server.
Append 'TYPE-QUERY' as query parameter in request url.
Specify `BOUNDARY' used to separate files in request header."
  (let ((url-request-method (if force "PUT" "PATCH"))
        (url-request-data (encode-coding-string body 'utf-8))
        (url-request-extra-headers `(("content-type" . ,(format "multipart/form-data; boundary=%s" boundary))
                                     ("Authorization" . ,(encode-coding-string (format "Bearer %s" khoj-api-key) 'utf-8)))))
      (with-current-buffer
          (url-retrieve (format "%s/api/content?%s&client=emacs" khoj-server-url type-query)
                        ;; render response from indexing API endpoint on server
                        (lambda (status)
                          (if (not (plist-get status :error))
                              (message "khoj.el: %scontent index %supdated" (if content-type (format "%s " content-type) "all ") (if force "force " ""))
                            (progn
                              (khoj--delete-open-network-connections-to-server)
                              (with-current-buffer (current-buffer)
                                (search-forward "\n\n" nil t)
                                (message "khoj.el: Failed to %supdate %scontent index. Status: %s%s"
                                         (if force "force " "")
                                         (if content-type (format "%s " content-type) "all")
                                         (string-trim (format "%s %s" (nth 1 (nth 1 status)) (nth 2 (nth 1 status))))
                                         (if (> (- (point-max) (point)) 0) (format ". Response: %s" (string-trim (buffer-substring-no-properties (point) (point-max)))) ""))))))
                        nil t t))))

(defun khoj--render-update-files-as-request-body (files-to-index boundary)
  "Render `FILES-TO-INDEX', `PREVIOUSLY-INDEXED-FILES' as multi-part form body.
Use `BOUNDARY' to separate files. This is sent to Khoj server as a POST request."
  (with-temp-buffer
    (set-buffer-multibyte nil)
    (insert "\n")
    (dolist (file-to-index files-to-index)
      ;; find file content-type. Choose from org, markdown, pdf, plaintext
      (let ((content-type (khoj--filename-to-mime-type file-to-index))
            (file-name (encode-coding-string  file-to-index 'utf-8)))
      (insert (format "--%s\r\n" boundary))
      (insert (format "Content-Disposition: form-data; name=\"files\"; filename=\"%s\"\r\n" file-name))
      (insert (format "Content-Type: %s\r\n\r\n" content-type))
      (insert (with-temp-buffer
                (insert-file-contents-literally file-to-index)
                (buffer-string)))
      (insert "\r\n")))
    (insert (format "--%s--\r\n" boundary))
    (buffer-string)))

(defun khoj--render-delete-files-as-request-body (delete-files boundary)
  "Render `DELETE-FILES' as multi-part form body.
Use `BOUNDARY' to separate files. This is sent to Khoj server as a POST request."
  (with-temp-buffer
    (set-buffer-multibyte nil)
    (insert "\n")
    (dolist (file-to-index delete-files)
      (let ((content-type (khoj--filename-to-mime-type file-to-index))
            (file-name (encode-coding-string  file-to-index 'utf-8)))
          (insert (format "--%s\r\n" boundary))
          (insert (format "Content-Disposition: form-data; name=\"files\"; filename=\"%s\"\r\n" file-name))
          (insert (format "Content-Type: %s\r\n\r\n" content-type))
          (insert "")
          (insert "\r\n")))
    (insert (format "--%s--\r\n" boundary))
    (buffer-string)))

(defun khoj--filename-to-mime-type (file-name)
  "`FILE-NAME' to mimeType."
  (cond ((string-match "\\.org$" file-name) "text/org")
        ((string-match "\\.\\(md\\|markdown\\)$" file-name) "text/markdown")
        ((string-match "\\.pdf$" file-name) "application/pdf")
        (t "text/plain")))

;; Cancel any running indexing timer, first
(when khoj--index-timer
    (cancel-timer khoj--index-timer))
;; Send files to index on server every `khoj-index-interval' seconds
(when khoj-auto-index
  (setq khoj--index-timer
        (run-with-timer 60 khoj-index-interval 'khoj--server-index-files)))


;; -------------------------------------------
;; Render Response from Khoj server for Emacs
;; -------------------------------------------
(defun khoj--construct-find-similar-title (query)
  "Construct title for find-similar QUERY."
  (format "Similar to: %s"
          (replace-regexp-in-string "^[#\\*]* " "" (car (split-string query "\n")))))

(defun khoj--extract-entries-as-markdown (json-response query is-find-similar)
  "Convert JSON-RESPONSE, QUERY from API to markdown entries.
Use IS-FIND-SIMILAR bool to filter out first result.
As first result is the current entry at point."
  (thread-last
    json-response
    ;; filter our first result if is find similar as it'll be the current entry at point
    ((lambda (response) (if is-find-similar (seq-drop response 1) response)))
    ;; Extract and render each markdown entry from response
    (mapcar (lambda (json-response-item)
              (thread-last
                ;; Extract markdown entry from each item in json response
                (cdr (assoc 'entry json-response-item))
                ;; Format markdown entry as a string
                (format "%s\n\n")
                ;; Standardize results to 2nd level heading for consistent rendering
                (replace-regexp-in-string "^\#+" "##"))))
    ;; Render entries into markdown formatted string with query set as as top level heading
    (format "# %s\n%s" (if is-find-similar (khoj--construct-find-similar-title query) query))
    ;; remove leading (, ) or SPC from extracted entries string
    (replace-regexp-in-string "^[\(\) ]" "")))

(defun khoj--extract-entries-as-org (json-response query is-find-similar)
  "Convert JSON-RESPONSE, QUERY from API to `org-mode' entries.
Use IS-FIND-SIMILAR bool to filter out first result.
As first result is the current entry at point."
  (thread-last
    json-response
    ;; filter our first result if is find similar as it'll be the current entry at point
    ((lambda (response) (if is-find-similar (seq-drop response 1) response)))
    ;; Extract and render each org-mode entry from response
    (mapcar (lambda (json-response-item)
              (thread-last
                ;; Extract org entry from each item in json response
                (cdr (assoc 'entry json-response-item))
                ;; Format org entry as a string
                (format "%s")
                ;; Standardize results to 2nd level heading for consistent rendering
                (replace-regexp-in-string "^\*+" "**"))))
    ;; Render entries into org formatted string with query set as as top level heading
    (format "* %s\n%s\n" (if is-find-similar (khoj--construct-find-similar-title query) query))
    ;; remove leading (, ) or SPC from extracted entries string
    (replace-regexp-in-string "^[\(\) ]" "")))

(defun khoj--extract-entries-as-pdf (json-response query is-find-similar)
  "Convert JSON-RESPONSE, QUERY from API to PDF entries.
Use IS-FIND-SIMILAR bool to filter out first result.
As first result is the current entry at point."
  (thread-last
    json-response
    ;; filter our first result if is find similar as it'll be the current entry at point
    ((lambda (response) (if is-find-similar (seq-drop response 1) response)))
    ;; Extract and render each pdf entry from response
    (mapcar (lambda (json-response-item)
              (thread-last
                ;; Extract pdf entry from each item in json response
                (cdr (assoc 'compiled (assoc 'additional json-response-item)))
                ;; Format pdf entry as a org entry string
                (format "** %s\n\n"))))
    ;; Render entries into org formatted string with query set as as top level heading
    (format "* %s\n%s\n" (if is-find-similar (khoj--construct-find-similar-title query) query))
    ;; remove leading (, ) or SPC from extracted entries string
    (replace-regexp-in-string "^[\(\) ]" "")))

(defun khoj--extract-entries-as-images (json-response query)
  "Convert JSON-RESPONSE, QUERY from API to html with images."
  (let ((image-results-buffer-html-format-str "<html>\n<body>\n<h1>%s</h1>%s\n\n</body>\n</html>")
        ;; Format string to wrap images into html img, href tags with metadata in headings
        (image-result-html-format-str "\n\n<h2>Score: %s Meta: %s Image: %s</h2>\n\n<a href=\"%s\">\n<img src=\"%s?%s\" width=%s height=%s>\n</a>"))
    (thread-last
      json-response
      ;; Extract each image entry from response and render as html
      (mapcar (lambda (json-response-item)
                (let ((score (cdr (assoc 'score json-response-item)))
                      (metadata_score (cdr (assoc 'metadata_score (assoc 'additional json-response-item))))
                      (image_score (cdr (assoc 'image_score (assoc 'additional json-response-item))))
                      (image_url (concat khoj-server-url (cdr (assoc 'entry json-response-item)))))
                  ;; Wrap images into html img, href tags with metadata in headings
                  (format image-result-html-format-str
                          ;; image scores metadata
                          score metadata_score image_score
                          ;; image url
                          image_url image_url (random 10000)
                          ;; image dimensions
                          khoj-image-width khoj-image-height))))
      ;; Collate entries into single html page string
      (format image-results-buffer-html-format-str query)
      ;; remove leading (, ) or SPC from extracted entries string
      (replace-regexp-in-string "^[\(\) ]" "")
      ;; remove trailing (, ) or SPC from extracted entries string
      (replace-regexp-in-string "[\(\) ]$" ""))))

(defun khoj--extract-entries (json-response query is-find-similar)
  "Convert JSON-RESPONSE, QUERY from API to text entries.
Use IS-FIND-SIMILAR bool to filter out first result.
As first result is the current entry at point."
  (thread-last json-response
               ;; filter our first result if is find similar as it'll be the current entry at point
               ((lambda (response) (if is-find-similar (seq-drop response 1) response)))
               ;; extract and render entries from API response
               (mapcar (lambda (json-response-item)
                         (thread-last
                           ;; Extract pdf entry from each item in json response
                           (cdr (assoc 'entry json-response-item))
                           (format "%s\n\n")
                           ;; Standardize results to 2nd level heading for consistent rendering
                           (replace-regexp-in-string "^\*+" "")
                           ;; Standardize results to 2nd level heading for consistent rendering
                           (replace-regexp-in-string "^\#+" "")
                           ;; Format entries as org entry string
                           (format "** %s"))))
               ;; Set query as heading in rendered results buffer
               (format "* %s\n%s\n" (if is-find-similar (khoj--construct-find-similar-title query) query))
               ;; remove leading (, ) or SPC from extracted entries string
               (replace-regexp-in-string "^[\(\) ]" "")
               ;; remove trailing (, ) or SPC from extracted entries string
               (replace-regexp-in-string "[\(\) ]$" "")))

(defun khoj--buffer-name-to-content-type (buffer-name)
  "Infer content type based on BUFFER-NAME."
  (let ((enabled-content-types (khoj--get-enabled-content-types))
        (file-extension (file-name-extension buffer-name)))
    (cond
     ((and (member 'org enabled-content-types) (equal file-extension "org")) "org")
     ((and (member 'pdf enabled-content-types) (equal file-extension "pdf")) "pdf")
     ((and (member 'markdown enabled-content-types) (or (equal file-extension "markdown") (equal file-extension "md"))) "markdown")
     (t khoj-default-content-type))))


(defun khoj--org-cycle-content (&optional arg)
  "Show all headlines in the buffer, like a table of contents.
With numerical argument ARG, show content up to level ARG.

Simplified fork of `org-cycle-content' from Emacs 29.1 to work with >=27.1."
  (interactive "p")
  (save-excursion
    (goto-char (point-max))
    (let ((regexp (if (and (wholenump arg) (> arg 0))
                      (format "^\\*\\{1,%d\\} " arg)
                    "^\\*+ "))
          (last (point)))
      (while (re-search-backward regexp nil t)
        (org-fold-region (line-end-position) last t 'outline)
        (setq last (line-end-position 0))))))


;; --------------
;; Query Khoj API
;; --------------
(defun khoj--call-api (path &optional method params body callback &rest cbargs)
  "Sync call API at PATH with METHOD, query PARAMS and BODY as kv assoc list.
Optionally apply CALLBACK with JSON parsed response and CBARGS."
  (let* ((url-request-method (or method "GET"))
         (url-request-extra-headers `(("Authorization" . ,(encode-coding-string (format "Bearer %s" khoj-api-key) 'utf-8))
                                      ("Content-Type" . "application/json")))
         (url-request-data (if body (encode-coding-string (json-encode body) 'utf-8) nil))
         (param-string (url-build-query-string (append params '((client "emacs")))))
         (query-url (format "%s%s?%s" khoj-server-url path param-string))
         (cbargs (if (and (listp cbargs) (listp (car cbargs))) (car cbargs) cbargs))) ; normalize cbargs to (a b) from ((a b)) if required
    (with-temp-buffer
      (condition-case ex
          (progn
            (url-insert-file-contents query-url)
            (if (and callback cbargs)
                (apply callback (json-parse-buffer :object-type 'alist) cbargs)
              (if callback
                  (funcall callback (json-parse-buffer :object-type 'alist))
            (json-parse-buffer :object-type 'alist))))
        ('file-error (message "Chat exception: [%s]" ex))))))

(defun khoj--call-api-async (path &optional method params body callback &rest cbargs)
  "Async call to API at PATH with specified METHOD, query PARAMS and request BODY.
Optionally apply CALLBACK with JSON parsed response and CBARGS."
  (let* ((url-request-method (or method "GET"))
         (url-request-extra-headers `(("Authorization" . ,(encode-coding-string (format "Bearer %s" khoj-api-key) 'utf-8))
                                      ("Content-Type" . "application/json")))
         (url-request-data (if body (encode-coding-string (json-encode body) 'utf-8) nil))
         (param-string (url-build-query-string (append params '((client "emacs")))))
         (query-url (format "%s%s?%s" khoj-server-url path param-string))
         (cbargs (if (and (listp cbargs) (listp (car cbargs))) (car cbargs) cbargs))) ; normalize cbargs to (a b) from ((a b)) if required
    (url-retrieve query-url
                  (lambda (status)
                    (if (plist-get status :error)
                        (message "Chat exception: [%s]" (plist-get status :error))
                      (goto-char (point-min))
                      (re-search-forward "^$")
                      (delete-region (point) (point-min))
                      (if (and callback cbargs)
                          (apply callback (json-parse-buffer :object-type 'alist) cbargs)
                        (if callback
                            (funcall callback (json-parse-buffer :object-type 'alist))
                          (json-parse-buffer :object-type 'alist))))))))

(defun khoj--get-enabled-content-types ()
  "Get content types enabled for search from API."
  (khoj--call-api "/api/content/types" "GET" nil nil `(lambda (item) (mapcar #'intern item))))

(defun khoj--query-search-api-and-render-results (query content-type buffer-name &optional rerank is-find-similar)
  "Query Khoj Search API with QUERY, CONTENT-TYPE and RERANK as query params.
Render search results in BUFFER-NAME using CONTENT-TYPE and QUERY.
Filter out first similar result if IS-FIND-SIMILAR set."
  (let* ((rerank (or rerank "false"))
         (params `((q ,(encode-coding-string query 'utf-8))
                   (t ,content-type)
                   (r ,rerank)
                   (n ,khoj-results-count)))
         (path "/api/search"))
    (khoj--call-api-async path
                    "GET"
                    params
                    nil
                    'khoj--render-search-results
                    content-type query buffer-name is-find-similar)))

(defun khoj--render-search-results (json-response content-type query buffer-name &optional is-find-similar)
  "Render search results in BUFFER-NAME using JSON-RESPONSE, CONTENT-TYPE, QUERY.
Filter out first similar result if IS-FIND-SIMILAR set."
  ;; render json response into formatted entries
  (with-current-buffer buffer-name
    (let ((is-find-similar (or is-find-similar nil))
          (inhibit-read-only t))
      (erase-buffer)
      (insert
       (cond ((equal content-type "org") (khoj--extract-entries-as-org json-response query is-find-similar))
             ((equal content-type "markdown") (khoj--extract-entries-as-markdown json-response query is-find-similar))
             ((equal content-type "pdf") (khoj--extract-entries-as-pdf json-response query is-find-similar))
             ((equal content-type "image") (khoj--extract-entries-as-images json-response query))
             (t (khoj--extract-entries json-response query is-find-similar))))
      (cond ((or (equal content-type "all")
                 (equal content-type "pdf")
                 (equal content-type "org"))
             (progn (visual-line-mode)
                    (org-mode)
                    (setq-local
                     org-hide-leading-stars t
                     org-startup-with-inline-images t)
                    (khoj--org-cycle-content 2)))
            ((equal content-type "markdown") (progn (markdown-mode)
                                                    (visual-line-mode)))
            ((equal content-type "image") (progn (shr-render-region (point-min) (point-max))
                                                 (goto-char (point-min))))
            (t (fundamental-mode))))
    ;; keep cursor at top of khoj buffer by default
    (goto-char (point-min))
    ;; enable minor modes for khoj chat
    (visual-line-mode)
    (read-only-mode t)))


;; ----------------
;; Khoj Chat
;; ----------------

(defun khoj--chat (&optional session-id)
  "Chat with Khoj in session with SESSION-ID."
  (interactive)
  (when (or session-id (not (get-buffer khoj--chat-buffer-name)))
    (khoj--load-chat-session khoj--chat-buffer-name session-id))
  (let ((query (read-string "Query: ")))
    (when (not (string-empty-p query))
      (khoj--query-chat-api-and-render-messages query khoj--chat-buffer-name session-id))))

(defun khoj--open-side-pane (buffer-name)
  "Open Khoj BUFFER-NAME in right side pane."
  (save-selected-window
    (if (get-buffer-window-list buffer-name)
        ;; if window is already open, switch to it
        (progn
          (select-window (get-buffer-window buffer-name))
          (switch-to-buffer buffer-name))
      ;; else if window is not open, open it as a right-side window pane
      (let ((bottomright-window (some-window (lambda (window) (and (window-at-side-p window 'right) (window-at-side-p window 'bottom))))))
        (progn
          ;; Select the right-most window
          (select-window bottomright-window)
          ;; if bottom-right window is not a vertical pane, split it vertically, else use the existing bottom-right vertical window
          (let ((khoj-window (if (window-at-side-p bottomright-window 'left)
                                 (split-window-right)
                               bottomright-window)))
            ;; Set the buffer in the khoj window
            (set-window-buffer khoj-window buffer-name)
            ;; Switch to the khoj window
            (select-window khoj-window)
            ;; Resize the window to 1/3 of the frame width
            (window-resize khoj-window
                           (- (truncate (* 0.33 (frame-width))) (window-width))
                           t)))))
    (goto-char (point-max))))

(defun khoj--load-chat-session (buffer-name &optional session-id)
  "Load Khoj Chat conversation history from SESSION-ID into BUFFER-NAME."
  (setq khoj--reference-count 0)
  (let ((inhibit-read-only t)
        (json-response (cdr (assoc 'chat (cdr (assoc 'response (khoj--get-chat-session session-id)))))))
    (with-current-buffer (get-buffer-create buffer-name)
      (progn
        (erase-buffer)
        (insert "* Khoj Chat\n")
        (when json-response
          (thread-last
            json-response
            ;; generate chat messages from Khoj Chat API response
            (mapcar #'khoj--format-chat-response)
            ;; insert chat messages into Khoj Chat Buffer
            (mapc #'insert)))
        (org-mode)
        ;; commented add-hover-text func due to perf issues with the implementation
        ;;(khoj--add-hover-text-to-footnote-refs (point-min))
        ;; render reference footnotes as superscript
        (setq-local
         org-startup-folded "showall"
         org-hide-leading-stars t
         org-use-sub-superscripts '{}
         org-pretty-entities-include-sub-superscripts t
         org-pretty-entities t)
        (org-set-startup-visibility)

        ;; create khoj chat shortcut keybindings
        (use-local-map (copy-keymap org-mode-map))
        (local-set-key (kbd "q") #'khoj--close)
        (local-set-key (kbd "m") #'khoj--chat)
        (local-set-key (kbd "C-x m") #'khoj--chat)

        ;; enable minor modes for khoj chat
        (visual-line-mode)
        (read-only-mode t)))
    (khoj--open-side-pane buffer-name)))

(defun khoj--close ()
  "Kill Khoj buffer and window."
  (interactive)
  (progn
    (kill-buffer (current-buffer))
    (delete-window)))

(defun khoj--add-hover-text-to-footnote-refs (start-pos)
  "Show footnote defs on mouse hover on footnote refs from START-POS."
  (org-with-wide-buffer
   (goto-char start-pos)
   (while (re-search-forward org-footnote-re nil t)
     (backward-char)
     (let* ((context (org-element-context))
            (label (org-element-property :label context))
            (footnote-def (nth 3 (org-footnote-get-definition label)))
            (footnote-width (if (< (length footnote-def) 70) nil 70))
            (begin-pos (org-element-property :begin context))
            (end-pos (org-element-property :end context))
            (overlay (make-overlay begin-pos end-pos)))
       (when (memq (org-element-type context)
                   '(footnote-reference))
         (-->
          footnote-def
          ;; truncate footnote definition if required
          (substring it 0 footnote-width)
          ;; append continuation suffix if truncated
          (concat it (if footnote-width "..." ""))
          ;; show definition on hover on footnote reference
          (overlay-put overlay 'help-echo it)))))))

(defun khoj--query-chat-api-and-render-messages (query buffer-name &optional session-id)
  "Send QUERY to Chat SESSION-ID. Render the chat messages in BUFFER-NAME."
  ;; render json response into formatted chat messages
  (with-current-buffer (get-buffer buffer-name)
    (let ((inhibit-read-only t)
          (query-time (format-time-string "%F %T")))
      (goto-char (point-max))
      (insert
       (khoj--render-chat-message query "you" query-time))
      (khoj--query-chat-api query
                            session-id
                            #'khoj--format-chat-response
                            #'khoj--render-chat-response buffer-name))))

(defun khoj--query-chat-api (query session-id callback &rest cbargs)
  "Send QUERY for SESSION-ID to Khoj Chat API.
Call CALLBACK func with response and CBARGS."
  (let ((params `(("q" . ,query) ("n" . ,khoj-results-count))))
    (when session-id (push `("conversation_id" . ,session-id) params))
    (khoj--call-api-async "/api/chat"
                          "POST"
                          nil
                          params
                          callback cbargs)))

(defun khoj--get-chat-sessions ()
  "Get all chat sessions from Khoj server."
  (khoj--call-api "/api/chat/sessions" "GET"))

(defun khoj--get-chat-session (&optional session-id)
  "Get chat messages from default or SESSION-ID chat session."
  (khoj--call-api "/api/chat/history"
                  "GET"
                  (when session-id `(("conversation_id" ,session-id)))))

(defun khoj--select-conversation-session (&optional completion-action)
  "Select Khoj conversation session to perform COMPLETION-ACTION on."
  (let* ((completion-text (format "%s Conversation:" (or completion-action "Open")))
         (sessions (khoj--get-chat-sessions))
         (session-alist (-map (lambda (session)
                                (cons (if (not (equal :null (cdr (assoc 'slug session))))
                                          (cdr (assoc 'slug session))
                                        (format "New Conversation (%s)" (cdr (assoc 'conversation_id session))))
                                      (cdr (assoc 'conversation_id session))))
                              sessions))
         (selected-session-slug (completing-read completion-text session-alist nil t)))
    (cdr (assoc selected-session-slug session-alist))))

(defun khoj--open-conversation-session ()
  "Menu to select Khoj conversation session to open."
  (let ((selected-session-id (khoj--select-conversation-session "Open")))
    (khoj--load-chat-session khoj--chat-buffer-name selected-session-id)))

(defun khoj--create-chat-session (&optional agent)
  "Create new chat session with AGENT."
  (khoj--call-api "/api/chat/sessions"
                  "POST"
                  (when agent `(("agent_slug" ,agent)))))

(defun khoj--new-conversation-session (&optional agent)
  "Create new Khoj conversation session with AGENT."
  (thread-last
    (khoj--create-chat-session agent)
    (assoc 'conversation_id)
    (cdr)
    (khoj--chat)))

(defun khoj--delete-chat-session (session-id)
  "Delete chat session with SESSION-ID."
  (khoj--call-api "/api/chat/history" "DELETE" `(("conversation_id" ,session-id))))

(defun khoj--delete-conversation-session ()
  "Delete new Khoj conversation session."
  (thread-last
    (khoj--select-conversation-session "Delete")
    (khoj--delete-chat-session)))

(defun khoj--get-agents ()
  "Get list of available Khoj agents."
  (let* ((response (khoj--call-api "/api/agents" "GET"))
         (agents (mapcar (lambda (agent)
                           (cons (cdr (assoc 'name agent))
                                 (cdr (assoc 'slug agent))))
                         response)))
    agents))

(defun khoj--render-chat-message (message sender &optional receive-date)
  "Render chat messages as `org-mode' list item.
MESSAGE is the text of the chat message.
SENDER is the message sender.
RECEIVE-DATE is the message receive date."
  (let ((first-message-line (car (split-string message "\n" t)))
        (rest-message-lines (string-join (cdr (split-string message "\n" t)) "\n"))
        (heading-level (if (equal sender "you") "**" "***"))
        (emojified-sender (if (equal sender "you") "🤔 *You*" "🏮 *Khoj*"))
        (suffix-newlines (if (equal sender "khoj") "\n\n" ""))
        (received (or receive-date (format-time-string "%F %T"))))
    (format "%s %s: %s\n   :PROPERTIES:\n   :RECEIVED: [%s]\n   :END:\n%s\n%s"
            heading-level
            emojified-sender
            first-message-line
            received
            rest-message-lines
            suffix-newlines)))

(defun khoj--generate-reference (reference)
  "Create `org-mode' footnotes with REFERENCE."
  (setq khoj--reference-count (1+ khoj--reference-count))
  (let ((compiled-reference (if (stringp reference) reference (cdr (assoc 'compiled reference)))))
    (cons
     (propertize (format "^{ [fn:%x]}" khoj--reference-count) 'help-echo compiled-reference)
     (thread-last
       compiled-reference
       ;; remove filename top heading line from reference
       ;; prevents actual reference heading in next line jumping out of references footnote section
       (replace-regexp-in-string "^\* .*\n" "")
       ;; remove multiple, consecutive empty lines from reference
       (replace-regexp-in-string "\n\n" "\n")
       (format "\n[fn:%x] %s" khoj--reference-count)))))

(defun khoj--generate-online-reference (reference)
  "Create `org-mode' footnotes for online REFERENCE."
  (setq khoj--reference-count (1+ khoj--reference-count))
  (let* ((link (cdr (assoc 'link reference)))
        (title (or (cdr (assoc 'title reference)) link))
        (description (or (cdr (assoc 'description reference)) title)))
    (cons
     (propertize (format "^{ [fn:%x]}" khoj--reference-count) 'help-echo (format "%s\n%s" link description))
     (thread-last
       description
       ;; remove multiple, consecutive empty lines from reference
       (replace-regexp-in-string "\n\n" "\n")
       (format "\n[fn:%x] [[%s][%s]]\n%s\n" khoj--reference-count link title)))))

(defun khoj--extract-online-references (result-types query-result-pairs)
  "Extract link, title and description from RESULT-TYPES in QUERY-RESULT-PAIRS."
  (let ((result '()))
    (-map
     (lambda (search)
      (let ((search-q (car search))
            (search-results (cdr search)))
        (-map-when
         ;; filter search results by specified result types
         (lambda (search-result) (member (car search-result) result-types))
         ;; extract link, title, and description from search results
         (lambda (search-result)
           (-map
            (lambda (entry)
              (let* ((link (cdr (or (assoc 'link entry) (assoc 'descriptionLink entry))))
                     (title (cdr (or (assoc 'title entry) `(title . ,link))))
                     (description (cdr (or (assoc 'snippet entry) (assoc 'description entry)))))
                (setq result (append result `(((title . ,title) (link . ,link) (description . ,description) (search . ,search-q)))))))
            ;; wrap search results in a list if it is not already a list
            (if (or (equal 'knowledgeGraph (car search-result)) (equal 'webpages (car search-result)))
                (if (arrayp (cdr search-result))
                    (list (elt (cdr search-result) 0))
                  (list (cdr search-result)))
              (cdr search-result))))
         search-results)))
     query-result-pairs)
    result))

(defun khoj--render-chat-response (response buffer-name)
  "Insert chat message from RESPONSE into BUFFER-NAME."
  (with-current-buffer (get-buffer buffer-name)
    (let ((start-pos (point))
          (inhibit-read-only t))
      (goto-char (point-max))
      (insert
       response
       (or (khoj--add-hover-text-to-footnote-refs start-pos) ""))
      (progn
        (org-set-startup-visibility)
        (visual-line-mode)
        (re-search-backward "^\*+ 🏮" nil t)))))

(defun khoj--format-chat-response (json-response &optional callback &rest cbargs)
  "Format chat message using JSON-RESPONSE from Khoj Chat API.
Run CALLBACK with CBARGS on formatted message."
  (let* ((message (cdr (or (assoc 'response json-response) (assoc 'message json-response))))
         (sender (cdr (assoc 'by json-response)))
         (receive-date (cdr (assoc 'created json-response)))
         (online-references  (or (cdr (assoc 'onlineContext json-response)) '()))
         (online-footnotes (-map #'khoj--generate-online-reference
                                 (khoj--extract-online-references '(organic knowledgeGraph peopleAlsoAsk webpages)
                                                                  online-references)))
         (doc-references (or (cdr (assoc 'context json-response)) '()))
         (doc-footnotes (mapcar #'khoj--generate-reference doc-references))
         (footnote-links (mapcar #'car (append doc-footnotes online-footnotes)))
         (footnote-defs (mapcar #'cdr (append doc-footnotes online-footnotes)))
         (formatted-response
          (thread-first
            ;; concatenate khoj message and references from API
            (concat
             message
             ;; append reference links to khoj message
             (string-join footnote-links "")
             ;; append reference sub-section to khoj message and fold it
             (if footnote-defs "\n**** References\n:PROPERTIES:\n:VISIBILITY: folded\n:END:" "")
             ;; append reference definitions to references subsection
             (string-join footnote-defs " "))
            ;; Render chat message using data obtained from API
            (khoj--render-chat-message sender receive-date))))
    (if callback
        (apply callback formatted-response cbargs)
        formatted-response)))


;; ------------------
;; Incremental Search
;; ------------------

(defun khoj--incremental-search (&optional rerank)
  "Perform Incremental Search on Khoj. Allow optional RERANK of results."
  (let* ((rerank-str (cond (rerank "true") (t "false")))
         (khoj-buffer-name (get-buffer-create khoj--search-buffer-name))
         (query (minibuffer-contents-no-properties)))
    ;; Query khoj API only when user in khoj minibuffer and non-empty query
    ;; Prevents querying if
    ;;   1. user hasn't started typing query
    ;;   2. during recursive edits
    ;;   3. with contents of other buffers user may jump to
    ;;   4. search not triggered right after rerank
    ;;      ignore to not overwrite reranked results before the user even sees them
    (if khoj--rerank
        (setq khoj--rerank nil)
      (when
          (and
           (not (equal query ""))
           (active-minibuffer-window)
           (equal (current-buffer) khoj--minibuffer-window))
      (progn
        (when rerank
          (setq khoj--rerank t)
          (message "khoj.el: Rerank Results"))
        (khoj--query-search-api-and-render-results
         query
         khoj--content-type
         khoj-buffer-name
         rerank-str))))))

(defun khoj--delete-open-network-connections-to-server ()
  "Delete all network connections to khoj server."
  (dolist (proc (process-list))
    (let ((proc-buf (buffer-name (process-buffer proc)))
          (khoj-network-proc-buf (string-join (split-string khoj-server-url "://") " ")))
      (when (string-match (format "%s" khoj-network-proc-buf) proc-buf)
        (ignore-errors (delete-process proc))))))

(defun khoj--teardown-incremental-search ()
  "Teardown hooks used for incremental search."
  (message "khoj.el: Teardown Incremental Search")
  ;; unset khoj minibuffer window
  (setq khoj--minibuffer-window nil)
  (when (and khoj--search-on-idle-timer
             (timerp khoj--search-on-idle-timer))
    (cancel-timer khoj--search-on-idle-timer))
  ;; delete open connections to khoj server
  (khoj--delete-open-network-connections-to-server)
  ;; remove hooks for khoj incremental query and self
  (remove-hook 'post-command-hook #'khoj--incremental-search)
  (remove-hook 'minibuffer-exit-hook #'khoj--teardown-incremental-search))

(defun khoj-incremental ()
  "Natural, Incremental Search for your personal notes and documents."
  (interactive)
  (let* ((khoj-buffer-name (get-buffer-create khoj--search-buffer-name)))
    ;; switch to khoj search buffer
    (khoj--open-side-pane khoj-buffer-name)
    ;; open and setup minibuffer for incremental search
    (minibuffer-with-setup-hook
        (lambda ()
          ;; Add khoj keybindings for configuring search to minibuffer keybindings
          (khoj--make-search-keymap minibuffer-local-map)
          ;; Display information on keybindings to customize khoj search
          (khoj--display-keybinding-info)
          ;; set current (mini-)buffer entered as khoj minibuffer
          ;; used to query khoj API only when user in khoj minibuffer
          (setq khoj--minibuffer-window (current-buffer))
          ; do khoj incremental search after idle time
          (setq khoj--search-on-idle-timer (run-with-idle-timer khoj-search-on-idle-time t #'khoj--incremental-search))
          ; teardown khoj incremental search on minibuffer exit
          (add-hook 'minibuffer-exit-hook #'khoj--teardown-incremental-search))
      (read-string khoj--query-prompt))))


;; --------------
;; Similar Search
;; --------------

(defun khoj--get-current-outline-entry-pos ()
  "Get heading position of current outline section."
  ;; get heading position of current outline entry
  (cond
   ;; when at heading of entry
   ((looking-at outline-regexp)
    (point))
   ;; when within entry
   (t (save-excursion (outline-previous-heading) (point)))))

(defun khoj--get-current-outline-entry-text ()
  "Get text under current outline section."
  (string-trim
   ;; get text of current outline entry
   (cond
    ;; when at heading of entry
    ((looking-at outline-regexp)
     (buffer-substring-no-properties
      (point)
      (save-excursion (outline-next-heading) (point))))
    ;; when within entry
    (t (buffer-substring-no-properties
        (save-excursion (outline-previous-heading) (point))
        (save-excursion (outline-next-heading) (point)))))))

(defun khoj--get-current-paragraph-text ()
  "Get trimmed text in current paragraph at point.
Paragraph only starts at first text after blank line."
  (string-trim
   (cond
    ;; when at end of a middle paragraph
    ((and (looking-at paragraph-start) (not (equal (point) (point-min))))
     (buffer-substring-no-properties
      (save-excursion (backward-paragraph) (point))
      (point)))
    ;; else
    (t (thing-at-point 'paragraph t)))))


(defun khoj--find-similar (&optional content-type)
  "Find items of CONTENT-TYPE in khoj index similar to text surrounding point."
  (interactive)
  (let* ((rerank "true")
         ;; set content type to: specified > based on current buffer > default type
         (content-type (or content-type (khoj--buffer-name-to-content-type (buffer-name))))
         ;; get text surrounding current point based on the major mode context
         (query (cond
                 ;; get section outline derived mode like org or markdown
                 ((or (derived-mode-p 'outline-mode) (equal major-mode 'markdown-mode))
                  (khoj--get-current-outline-entry-text))
                 ;; get paragraph, if in text mode
                 (t
                  (khoj--get-current-paragraph-text))))
         (buffer-name (get-buffer-create khoj--search-buffer-name)))
    (progn
      (khoj--query-search-api-and-render-results
       query
       content-type
       buffer-name
       rerank
       t)
      (khoj--open-side-pane buffer-name))))

(defun khoj--auto-find-similar ()
  "Call find similar on current element, if point has moved to a new element."
  ;; Call find similar
  (when (and (derived-mode-p 'org-mode)
             (org-element-at-point)
             (not (string= (buffer-name (current-buffer)) khoj--search-buffer-name))
             (get-buffer-window khoj--search-buffer-name))
    (let ((current-heading-pos (khoj--get-current-outline-entry-pos)))
      (unless (eq current-heading-pos khoj--last-heading-pos)
          (setq khoj--last-heading-pos current-heading-pos)
          (khoj--find-similar)))))

(defun khoj--setup-auto-find-similar ()
  "Setup automatic call to find similar to current element."
  (if khoj-auto-find-similar
      (add-hook 'post-command-hook #'khoj--auto-find-similar)
    (remove-hook 'post-command-hook #'khoj--auto-find-similar)))

(defun khoj-toggle-auto-find-similar ()
    "Toggle automatic call to find similar to current element."
    (interactive)
    (setq khoj-auto-find-similar (not khoj-auto-find-similar))
    (khoj--setup-auto-find-similar)
    (if khoj-auto-find-similar
        (message "Auto find similar enabled")
      (message "Auto find similar disabled")))


;; ---------
;; Khoj Menu
;; ---------

(defun khoj--setup-and-show-menu ()
  "Create main Transient menu for Khoj and show it."
  ;; Create the Khoj Transient menu
  (transient-define-argument khoj--content-type-switch ()
    :class 'transient-switches
    :argument-format "--content-type=%s"
    :argument-regexp ".+"
    ;; set content type to: last used > based on current buffer > default type
    :init-value (lambda (obj) (oset obj value (format "--content-type=%s" (or khoj--content-type (khoj--buffer-name-to-content-type (buffer-name))))))
    ;; dynamically set choices to content types enabled on khoj backend
    :choices (or (ignore-errors (mapcar #'symbol-name (khoj--get-enabled-content-types))) '("all" "org" "markdown" "pdf" "image")))

  (transient-define-argument khoj--agent-switch ()
    :class 'transient-switches
    :argument-format "--agent=%s"
    :argument-regexp ".+"
    :init-value (lambda (obj)
                  (oset obj value (format "--agent=%s" khoj--selected-agent)))
    :choices (or (ignore-errors (mapcar #'cdr (khoj--get-agents))) '("khoj"))
    :reader (lambda (prompt initial-input history)
              (let* ((agents (khoj--get-agents))
                    (selected (completing-read prompt agents nil t initial-input history))
                    (slug (cdr (assoc selected agents))))
                (setq khoj--selected-agent slug)
                slug)))

  (transient-define-suffix khoj--search-command (&optional args)
    (interactive (list (transient-args transient-current-command)))
    (progn
      ;; set content type to: specified > last used > based on current buffer > default type
      (setq khoj--content-type (or (transient-arg-value "--content-type=" args) (khoj--buffer-name-to-content-type (buffer-name))))
      ;; set results count to: specified > last used > to default
      (setq khoj-results-count (or (transient-arg-value "--results-count=" args) khoj-results-count))
      ;; trigger incremental search
      (call-interactively #'khoj-incremental)))

  (transient-define-suffix khoj--find-similar-command (&optional args)
    "Find items similar to current item at point."
    (interactive (list (transient-args transient-current-command)))
    (progn
      ;; set content type to: specified > last used > based on current buffer > default type
      (setq khoj--content-type (or (transient-arg-value "--content-type=" args) (khoj--buffer-name-to-content-type (buffer-name))))
      ;; set results count to: specified > last used > to default
      (setq khoj-results-count (or (transient-arg-value "--results-count=" args) khoj-results-count))
      (khoj--find-similar khoj--content-type)))

  (transient-define-suffix khoj--update-command (&optional args)
    "Call khoj API to update index of specified content type."
    (interactive (list (transient-args transient-current-command)))
    (let* ((force-update (if (member "--force-update" args) t nil))
           ;; set content type to: specified > last used > based on current buffer > default type
           (content-type (or (transient-arg-value "--content-type=" args) (khoj--buffer-name-to-content-type (buffer-name))))
           (url-request-method "GET"))
      (progn
        (setq khoj--content-type content-type)
        (khoj--server-index-files force-update content-type))))

  (transient-define-suffix khoj--chat-command (&optional _)
    "Command to Chat with Khoj."
    (interactive (list (transient-args transient-current-command)))
    (khoj--chat))

  (transient-define-suffix khoj--open-conversation-session-command (&optional _)
    "Command to select Khoj conversation sessions to open."
    (interactive (list (transient-args transient-current-command)))
    (khoj--open-conversation-session))

  (transient-define-suffix khoj--new-conversation-session-command (&optional args)
    "Command to select Khoj conversation sessions to open."
    (interactive (list (transient-args transient-current-command)))
    (let ((agent-slug (transient-arg-value "--agent=" args)))
      (khoj--new-conversation-session agent-slug)))

  (transient-define-suffix khoj--delete-conversation-session-command (&optional _)
    "Command to select Khoj conversation sessions to delete."
    (interactive (list (transient-args transient-current-command)))
    (khoj--delete-conversation-session))

  (transient-define-prefix khoj--chat-menu ()
    "Create the Khoj Chat Menu and Execute Commands."
    [["Configure"
      ("a" "Select Agent" khoj--agent-switch)]]
    [["Act"
      ("c" "Chat" khoj--chat-command)
      ("o" "Open Conversation" khoj--open-conversation-session-command)
      ("n" "New Conversation" khoj--new-conversation-session-command)
      ("d" "Delete Conversation" khoj--delete-conversation-session-command)
      ("q" "Quit" transient-quit-one)]])

  (transient-define-prefix khoj--menu ()
    "Create Khoj Menu to Configure and Execute Commands."
    [["Configure Search"
      ("-n" "Results Count" "--results-count=" :init-value (lambda (obj) (oset obj value (format "%s" khoj-results-count))))
      ("t" "Content Type" khoj--content-type-switch)]
     ["Configure Update"
      ("-f" "Force Update" "--force-update")]]
    [["Act"
      ("c" "Chat" khoj--chat-menu)
      ("s" "Search" khoj--search-command)
      ("f" "Find Similar" khoj--find-similar-command)
      ("u" "Update" khoj--update-command)
      ("q" "Quit" transient-quit-one)]])

  ;; Show the Khoj Transient menu
  (khoj--menu))


;; ----------
;; Entrypoint
;; ----------

;;;###autoload
(defun khoj ()
  "Search and chat with your knowledge base using your personal AI copilot.

Collaborate with Khoj to search, create, review and update your knowledge base.
Research across the internet & your documents from the comfort of Emacs."
  (interactive)
  (when khoj-auto-setup
    (khoj-setup t))
  (khoj--setup-and-show-menu))

(provide 'khoj)

;;; khoj.el ends here
