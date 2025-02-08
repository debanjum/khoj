const { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage, shell, session } = require('electron');
const todesktop = require("@todesktop/runtime");
const khojPackage = require('./package.json');

todesktop.init();

const fs = require('fs');
const {dialog} = require('electron');

const cron = require('cron').CronJob;
const axios = require('axios');

const KHOJ_URL = 'https://app.khoj.dev';

const Store = require('electron-store');

const textFileTypes = [
    // Default valid file extensions supported by Khoj
    'org', 'md', 'markdown', 'txt', 'html', 'xml',
    // Other valid text file extensions from https://google.github.io/magika/model/config.json
    'appleplist', 'asm', 'asp', 'batch', 'c', 'cs', 'css', 'csv', 'eml', 'go', 'html', 'ini', 'internetshortcut', 'java', 'javascript', 'json', 'latex', 'lisp', 'makefile', 'markdown', 'mht', 'mum', 'pem', 'perl', 'php', 'powershell', 'python', 'rdf', 'rst', 'rtf', 'ruby', 'rust', 'scala', 'shell', 'smali', 'sql', 'svg', 'symlinktext', 'txt', 'vba', 'winregistry', 'xml', 'yaml']
const binaryFileTypes = ['pdf', 'jpg', 'jpeg', 'png', 'webp']
const validFileTypes = textFileTypes.concat(binaryFileTypes);

const schema = {
    files: {
        type: 'array',
        items: {
            type: 'object',
            properties: {
                path: {
                    type: 'string'
                }
            }
        },
        default: []
    },
    folders: {
        type: 'array',
        items: {
            type: 'object',
            properties: {
                path: {
                    type: 'string'
                }
            }
        },
        default: []
    },
    khojToken: {
        type: 'string',
        default: ''
    },
    hostURL: {
        type: 'string',
        default: KHOJ_URL
    },
    lastSync: {
        type: 'array',
        items: {
            type: 'object',
            properties: {
                path: {
                    type: 'string'
                },
                datetime: {
                    type: 'string'
                }
            }
        }
    }
};

let syncing = false;
let state = {}
const store = new Store({ schema });

console.log(store);

// include the Node.js 'path' module at the top of your file
const path = require('path');

function handleSetTitle (event, title) {
    const webContents = event.sender
    const win = BrowserWindow.fromWebContents(webContents)
    win.setTitle(title)
    dialog.showOpenDialog({properties: ['openFile', 'openDirectory'] }).then(function (response) {
        if (!response.canceled) {
            // handle fully qualified file name
          console.log(response.filePaths[0]);
        } else {
          console.log("no file selected");
        }
    });
}

function filenameToMimeType (filename) {
    const extension = filename.split('.').pop();
    switch (extension) {
        case 'pdf':
            return 'application/pdf';
        case 'png':
            return 'image/png';
        case 'jpg':
        case 'jpeg':
            return 'image/jpeg';
        case 'webp':
            return 'image/webp';
        case 'md':
        case 'markdown':
            return 'text/markdown';
        case 'org':
            return 'text/org';
        default:
            console.warn(`Unknown file type: ${extension}. Defaulting to text/plain.`);
            return 'text/plain';
    }
}

function isSupportedFileType(filePath) {
    const fileExtension = filePath.split('.').pop().toLowerCase();
    return validFileTypes.includes(fileExtension);
}

function processDirectory(filesToPush, folder) {
    try {
        const files = fs.readdirSync(folder.path, { withFileTypes: true });

        for (const file of files) {
            const filePath = path.join(file.path, file.name || '');
            // Skip hidden files and folders
            if (file.name.startsWith('.')) {
                continue;
            }
            // Add supported files to index
            if (file.isFile() && isSupportedFileType(filePath)) {
                console.log(`Add ${file.name} in ${file.path} for indexing`);
                filesToPush.push(filePath);
            }
            // Recursively process subdirectories
            if (file.isDirectory()) {
                processDirectory(filesToPush, {'path': filePath});
            }
        }
    } catch (err) {
        if (err.code === 'EACCES') {
            console.error(`Access denied to ${folder.path}`);
        } else if (err.code === 'ENOENT') {
            console.error(`${folder.path} does not exist`);
        } else {
            console.error(`An error occurred while reading directory: ${error.message}`);
        }
        return;
    }

}

function pushDataToKhoj (regenerate = false) {
    // Don't sync if token or hostURL is not set or if already syncing
    if (store.get('khojToken') === '' || store.get('hostURL') === '' || syncing === true) {
        const win = BrowserWindow.getAllWindows()[0];
        if (win) win.webContents.send('update-state', state);
        return;
    } else {
        syncing = true;
    }

    let filesToPush = [];
    const files = store.get('files') || [];
    const folders = store.get('folders') || [];
    state = { completed: true }

    // Collect paths of all configured files to index
    for (const file of files) {
        // Remove files that no longer exist
        if (!fs.existsSync(file.path)) {
            console.error(`${file.path} does not exist`);
            continue;
        }
        filesToPush.push(file.path);
    }

    // Collect paths of all indexable files in configured folders
    for (const folder of folders) {
        // Remove folders that no longer exist
        if (!fs.existsSync(folder.path)) {
            console.error(`${folder.path} does not exist`);
            continue;
        }
        processDirectory(filesToPush, folder);
    }

    const lastSync = store.get('lastSync') || [];
    const filesDataToPush = [];
    for (const file of filesToPush) {
        const stats = fs.statSync(file);
        if (!regenerate) {
            // Only push files that have been modified since last sync
            if (stats.mtime.toISOString() < lastSync.find((syncedFile) => syncedFile.path === file)?.datetime) {
                continue;
            }
        }

        // Collect all updated or newly created files since last sync to index on Khoj server
        try {
            let encoding = binaryFileTypes.includes(file.split('.').pop()) ? "binary" : "utf8";
            let mimeType = filenameToMimeType(file) + (encoding === "utf8" ? "; charset=UTF-8" : "");
            let fileContent = Buffer.from(fs.readFileSync(file, { encoding: encoding }), encoding);
            let fileObj = new Blob([fileContent], { type: mimeType });
            filesDataToPush.push({blob: fileObj, path: file});
            state[file] = {
                success: true,
            }
        } catch (err) {
            console.error(err);
            state[file] = {
                success: false,
                error: err
            }
        }
    }

    // Mark deleted files for removal from index on Khoj server
    for (const syncedFile of lastSync) {
        if (!filesToPush.includes(syncedFile.path)) {
            fileObj = new Blob([""], { type: filenameToMimeType(syncedFile.path) });
            filesDataToPush.push({blob: fileObj, path: syncedFile.path});
        }
    }

    // Send collected files to Khoj server for indexing
    const hostURL = store.get('hostURL') || KHOJ_URL;
    const headers = { 'Authorization': `Bearer ${store.get("khojToken")}` };
    let requests = [];

    // Request indexing files on server. With upto 1000 files in each request
    for (let i = 0; i < filesDataToPush.length; i += 1000) {
        const syncUrl = `${hostURL}/api/content?client=desktop`;
        const filesDataGroup = filesDataToPush.slice(i, i + 1000);
        const formData = new FormData();
        filesDataGroup.forEach(fileData => { formData.append('files', fileData.blob, fileData.path) });
        requests.push(
            regenerate
            ? axios.put(syncUrl, formData, { headers })
            : axios.patch(syncUrl, formData, { headers })
        );
    }

    // Wait for requests batch to finish
    Promise
    .all(requests)
    .then(responses => {
        const lastSync = filesToPush
            .filter(file => responses.find(response => response.data.includes(file)))
            .map(file => ({ path: file, datetime: new Date().toISOString() }));
        store.set('lastSync', lastSync);
    })
    .catch(error => {
        console.error(error);
        state["completed"] = false;
        if (error?.response?.status === 429 && (BrowserWindow.getAllWindows().find(win => win.webContents.getURL().includes('settings')))) {
            state["error"] = `Looks like you're out of space to sync your files. <a href="https://app.khoj.dev/settings#subscription">Upgrade your plan</a> to unlock more space.`;
            const win = BrowserWindow.getAllWindows().find(win => win.webContents.getURL().includes('settings'));
            if (win) win.webContents.send('needsSubscription', true);
        } else if (error?.code === 'ECONNREFUSED') {
            state["error"] = `Could not connect to Khoj server. Ensure you can connect to it at ${error.address}:${error.port}.`;
        } else {
            currentTime = new Date();
            state["error"] = `Sync was unsuccessful at ${currentTime.toLocaleTimeString()}. Contact team@khoj.dev to report this issue.`;
        }
    })
    .finally(() => {
        // Syncing complete
        syncing = false;
        const win = BrowserWindow.getAllWindows().find(win => win.webContents.getURL().includes('settings'));
        if (win) {
            win.webContents.send('update-state', state);
        }
    });
}

pushDataToKhoj();

async function handleFileOpen (type) {
    let { canceled, filePaths } = {canceled: true, filePaths: []};
    if (type === 'file') {
        ({ canceled, filePaths } = await dialog.showOpenDialog({properties: ['openFile' ], filters: [{ name: "Valid Khoj Files", extensions: validFileTypes }] }));
    } else if (type === 'folder') {
        ({ canceled, filePaths } = await dialog.showOpenDialog({properties: ['openDirectory' ]}));
    }
    if (!canceled) {
        const files = store.get('files') || [];
        const folders = store.get('folders') || [];

        for (const filePath of filePaths) {
            console.log(filePath);
            if (fs.existsSync(filePath)) {
                const stats = fs.statSync(filePath);
                if (stats.isFile()) {
                    console.log(`${filePath} is a file.`);

                    if (files.find((file) => file.path === filePath)) {
                        continue;
                    }

                    files.push({path: filePath});
                    store.set('files', files);
                } else if (stats.isDirectory()) {
                    console.log(`${filePath} is a directory.`);

                    if (folders.find((folder) => folder.path === filePath)) {
                        continue;
                    }

                    folders.push({path: filePath});
                    store.set('folders', folders);
                }

            } else {
                console.log(`${filePath} does not exist.`);
            }
        }
        return {
            files: store.get('files'),
            folders: store.get('folders')
        }
    }
}

async function getToken () {
    return store.get('khojToken');
}

async function setToken (event, token) {
    store.set('khojToken', token);
    return store.get('khojToken');
}

async function getFiles () {
    return store.get('files');
}

async function getFolders () {
    return store.get('folders');
}

async function setURL (event, url) {
    // Sanitize the URL. Remove trailing slash if present. Add http:// if not present.
    url = url.replace(/\/$/, "");
    if (!url.match(/^[a-zA-Z]+:\/\//)) {
        url = `http://${url}`;
    }

    store.set('hostURL', url);
    return store.get('hostURL');
}

async function getURL () {
    return store.get('hostURL');
}

async function removeFile (event, filePath) {
    const files = store.get('files');
    const newFiles = files.filter((file) => file.path !== filePath);
    store.set('files', newFiles);
    return newFiles;
}

async function removeFolder (event, folderPath) {
    const folders = store.get('folders');
    const newFolders = folders.filter((folder) => folder.path !== folderPath);
    store.set('folders', newFolders);
    return newFolders;
}

async function syncData (regenerate = false) {
    try {
        pushDataToKhoj(regenerate);
        const date = new Date();
        console.log('Pushing data to Khoj at: ', date);
    } catch (err) {
        console.error(err);
    }
}

async function deleteAllFiles () {
    try {
        store.set('files', []);
        store.set('folders', []);
        pushDataToKhoj(true);
        const date = new Date();
        console.log('Pushing data to Khoj at: ', date);
    } catch (err) {
        console.error(err);
    }
}

// Fetch user info from Khoj server
async function getUserInfo() {
    const getUserInfoURL = `${store.get('hostURL') || KHOJ_URL}/api/v1/user?client=desktop`;
    const headers = { 'Authorization': `Bearer ${store.get('khojToken')}` };
    try {
        let response = await axios.get(getUserInfoURL, { headers });
        return response.data;
    } catch (err) {
        console.error(err);
    }
}

function addCSPHeaderToSession () {
    // Get hostURL from store or use default
    const hostURL = store.get('hostURL') || KHOJ_URL;

    // Construct Content Security Policy
    const defaultDomains = `'self' ${hostURL} https://app.khoj.dev https://assets.khoj.dev`;
    const default_src = `default-src ${defaultDomains};`;
    const script_src = `script-src ${defaultDomains} 'unsafe-inline';`;
    const connect_src = `connect-src ${hostURL} https://ipapi.co/json;`;
    const style_src = `style-src ${defaultDomains} 'unsafe-inline' https://fonts.googleapis.com;`;
    const img_src = `img-src ${defaultDomains} data: https://*.khoj.dev https://*.googleusercontent.com;`;
    const font_src = `font-src https://fonts.gstatic.com;`;
    const child_src = `child-src 'none';`;
    const objectSrc = `object-src 'none';`;
    const csp = `${default_src} ${script_src} ${connect_src} ${style_src} ${img_src} ${font_src} ${child_src} ${objectSrc}`;

    // Add Content Security Policy to all web requests
    session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
        callback({
            responseHeaders: {
                ...details.responseHeaders,
                'Content-Security-Policy': [csp]
            }
        })
    })
 }

let firstRun = true;
let win = null;
let titleBarStyle = process.platform === 'win32' ? 'default' : 'hidden';
const {globalShortcut, clipboard} = require('electron'); // global shortcut and clipboard dependencies for shortcut window
const openShortcutWindowKeyBind = 'CommandOrControl+Shift+K'

const createWindow = (tab = '') => {
    win = new BrowserWindow({
      width: 800,
      height: 800,
      show: false,
      titleBarStyle: titleBarStyle,
      autoHideMenuBar: true,
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        nodeIntegration: true,
      }
    })

    const job = new cron('0 */10 * * * *', function() {
        try {
            pushDataToKhoj();
            const date = new Date();
            console.log('Pushing data to Khoj at: ', date);
            win.webContents.send('update-state', state);
        } catch (err) {
            console.error(err);
        }
    });

    win.setResizable(true);
    win.setOpacity(0.95);
    win.setBackgroundColor('#f5f4f3');
    win.setHasShadow(true);

    // Open external links in link handler registered on OS (e.g. browser)
    win.webContents.setWindowOpenHandler(async ({ url }) => {
        let shouldOpen = { response: 0 };

        if (!url.startsWith(store.get('hostURL'))) {
            // Confirm before opening external links
            const confirmNotice = `Do you want to open this link? It will be handled by an external application.\n\n${url}`;
            shouldOpen = await dialog.showMessageBox({
                type: 'question',
                buttons: ['Yes', 'No'],
                defaultId: 1,
                title: 'Confirm',
                message: confirmNotice,
            });
        }

        // If user confirms, let OS link handler open the link in appropriate app
        if (shouldOpen.response === 0) shell.openExternal(url);

        // Do not open external links within the app
        return { action: 'deny' };
    });

    job.start();

    const isWebApp = !tab?.endsWith('.html');
    const webAppBaseUrl = store.get('hostURL') || KHOJ_URL;
    const pageUrl = `${webAppBaseUrl}/${tab}`;
    isWebApp ? win.loadURL(pageUrl) : win.loadFile(tab);

    if (firstRun === true) {
        firstRun = false;

        // Create splash screen
        let splash = new BrowserWindow({width: 400, height: 400, transparent: true, frame: false, alwaysOnTop: true});
        splash.setOpacity(1.0);
        splash.setBackgroundColor('#d16b4e');
        splash.loadFile('splash.html');

        // Show splash screen on app load
        win.once('ready-to-show', () => {
            setTimeout(function(){ splash.close(); win.show(); }, 4500);
        });
    } else {
        // Show main window directly if not first run
        win.once('ready-to-show', () => { win.show(); });
    }
}

const createShortcutWindow = (tab = 'shortcut.html') => {
     var shortcutWin = new BrowserWindow({
         width: 400,
         height: 600,
         show: false,
         titleBarStyle: titleBarStyle,
         autoHideMenuBar: true,
         frame: false,
         webPreferences: {
             preload: path.join(__dirname, 'preload.js'),
             nodeIntegration: true,
         }
     });
     shortcutWin.setMenuBarVisibility(false);
     shortcutWin.setResizable(false);
     shortcutWin.setOpacity(0.95);
     shortcutWin.setBackgroundColor('#f5f4f3');
     shortcutWin.setHasShadow(true);
     shortcutWin.setVibrancy('popover');

     shortcutWin.loadFile(tab);
     shortcutWin.once('ready-to-show', () => {
         shortcutWin.show();
     });

    shortcutWin.on('closed', () => {
        shortcutWin = null;
    });

     return shortcutWin;
};

function isShortcutWindowOpen() {
     const windows = BrowserWindow.getAllWindows();
     for (let i = 0; i < windows.length; i++) {
         if (windows[i].webContents.getURL().endsWith('shortcut.html')) {
             return true;
         }
     }
     return false;
}

app.whenReady().then(() => {
    addCSPHeaderToSession();

    ipcMain.on('set-title', handleSetTitle);

    ipcMain.handle('handleFileOpen', (event, type) => {
        return handleFileOpen(type);
    });

    ipcMain.on('update-state', (event, arg) => {
        console.log(arg);
        event.reply('update-state', arg);
    });

    ipcMain.on('needsSubscription', (event, arg) => {
        console.log(arg);
        event.reply('needsSubscription', arg);
    });

    ipcMain.on('navigate', (event, page) => {
        win.loadFile(page);
    });

    ipcMain.on('navigateToWebApp', (event, page) => {
        shell.openExternal(`${store.get('hostURL')}/${page}`);
    });

    ipcMain.handle('getFiles', getFiles);
    ipcMain.handle('getFolders', getFolders);

    ipcMain.handle('removeFile', removeFile);
    ipcMain.handle('removeFolder', removeFolder);

    ipcMain.handle('setURL', setURL);
    ipcMain.handle('getURL', getURL);

    ipcMain.handle('setToken', setToken);
    ipcMain.handle('getToken', getToken);
    ipcMain.handle('getUserInfo', getUserInfo);

    ipcMain.handle('syncData', (event, regenerate) => {
        syncData(regenerate);
    });
    ipcMain.handle('deleteAllFiles', deleteAllFiles);

    ipcMain.handle('openFile', async (_, path) => {
        try {
            await shell.openPath(path);
        } catch (error) {
            console.error('Error opening file:', error);
        }
    });

    const mainWindow = createWindow();

    app.setAboutPanelOptions({
        applicationName: "Khoj",
        applicationVersion: khojPackage.version,
        version: khojPackage.version,
        authors: "Khoj AI",
        website: "https://khoj.dev",
        copyright: "GPL v3",
        iconPath: path.join(__dirname, 'assets', 'icons', 'favicon-128x128.png')
    });

    app.on('ready', async() => {
        try {
            const result = await todesktop.autoUpdater.checkForUpdates();
            if (result.updateInfo) {
              console.log("Desktop app update found:", result.updateInfo.version);
              todesktop.autoUpdater.restartAndInstall();
            }
          } catch (e) {
            console.warn("Desktop app update check failed:", e);
        }
    })
    globalShortcut.register(openShortcutWindowKeyBind, () => {
        console.log("Shortcut key pressed")
        if(isShortcutWindowOpen()) return;

        const shortcutWin = createShortcutWindow(); // Create a new shortcut window each time the shortcut is triggered
        shortcutWin.setAlwaysOnTop(true, 'screen-saver', 1);
        const clipboardText = clipboard.readText();
        console.log('Sending clipboard text:', clipboardText); // Debug log
        shortcutWin.webContents.once('dom-ready', () => {
          shortcutWin.webContents.send('clip', clipboardText);
          console.log('Message sent to window'); // Debug log
        });

        // Register a global shortcut for the Escape key for the shortcutWin
        globalShortcut.register('Escape', () => {
          if (shortcutWin) {
            shortcutWin.close();
          }
          // Unregister the Escape key shortcut
          globalShortcut.unregister('Escape');
        });

        shortcutWin.on('closed', () => {
            // Unregister the Escape key shortcut
            globalShortcut.unregister('Escape');
        });
        ipcMain.on('continue-conversation-button-clicked', () => {
            openWindow();
            if (shortcutWin && !shortcutWin.isDestroyed()) {
                shortcutWin.close();
            }
            // Unregister the Escape key shortcut
            globalShortcut.unregister('Escape');
        });
    });
    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
})

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit()
})

/*
** About Page
*/

let aboutWindow;

function openAboutWindow() {
    if (aboutWindow) { aboutWindow.focus(); return; }

    aboutWindow = new BrowserWindow({
        width: 400,
        height: 400,
        titleBarStyle: titleBarStyle,
        autoHideMenuBar: true,
        show: false,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            nodeIntegration: true,
        },
    });

    aboutWindow.loadFile('about.html');

    // Pass OS, Khoj version to About page
    aboutWindow.webContents.on('did-finish-load', () => {
        aboutWindow.webContents.send('appInfo', { version: khojPackage.version, platform: process.platform });
    });

    // Open links in external browser
    aboutWindow.webContents.setWindowOpenHandler(({ url }) => {
        shell.openExternal(url);
        return { action: 'deny' };
    });

    aboutWindow.once('ready-to-show', () => { aboutWindow.show(); });
    aboutWindow.on('closed', () => { aboutWindow = null; });
}

/*
**  System Tray Icon
*/

let tray

openWindow = (page = '') => {
    if (BrowserWindow.getAllWindows().length === 0) {
        createWindow(page);
    } else {
        const isWebApp = !page?.endsWith('.html');
        const webAppBaseUrl = store.get('hostURL') || KHOJ_URL;
        const pageUrl = isWebApp ? `${webAppBaseUrl}/${page}` : page;
        isWebApp ? win.loadURL(pageUrl) : win.loadFile(pageUrl);
        win.show();
    }
}

app.whenReady().then(() => {
    const iconPath = path.join(__dirname, './assets/icons/favicon-20x20.png')
    const icon = nativeImage.createFromPath(iconPath)
    tray = new Tray(icon)

    const contextMenu = Menu.buildFromTemplate([
        { label: 'Chat', type: 'normal', click: () => { openWindow(); }},
        { label: 'Search', type: 'normal', click: () => { openWindow('search') }},
        { label: 'Configure', type: 'normal', click: () => { openWindow('settings.html') }},
        { type: 'separator' },
        { label: 'About Khoj', type: 'normal', click: () => { openAboutWindow(); } },
        { label: 'Quit', type: 'normal', click: () => { app.quit() } }
    ])

    tray.setToolTip('Khoj')
    tray.setContextMenu(contextMenu)
})
