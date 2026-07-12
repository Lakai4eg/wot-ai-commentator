/* Stream Director — portable-лаунчер.
 * Читает current.txt, запускает апдейтер (он может переключить версию), затем
 * приложение. Если первый запуск после обновления провалился — предлагает
 * откат на предыдущую версию, которая ещё лежит в versions/.
 *
 * Сообщения в консоль — ASCII-английский: кодовая страница консоли на свежей
 * Windows портит кириллицу. В MessageBoxW кириллица отображается корректно. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

/* MessageBoxW живёт в user32.lib, а сама она, в отличие от kernel32.lib, к CRT
 * не прицеплена. Директива в объектнике избавляет от библиотеки в командной
 * строке — cl scripts\launcher.c слинкуется как есть. */
#pragma comment(lib, "user32.lib")

#define EXIT_SWITCHED 10

static void wait_for_enter(void) {
    fwprintf(stderr, L"Press Enter to exit...");
    getwchar();
}

/* Прочитать однострочный UTF-8 файл (версию). 0 — не вышло. */
static int read_line_file(const wchar_t *path, wchar_t *out, int cap) {
    FILE *f = _wfopen(path, L"rb");
    if (f == NULL) {
        return 0;
    }
    char buf[128] = {0};
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r' || buf[n - 1] == ' ')) {
        buf[--n] = '\0';
    }
    if (n == 0) {
        return 0;
    }
    return MultiByteToWideChar(CP_UTF8, 0, buf, -1, out, cap) != 0;
}

static void write_line_file(const wchar_t *path, const wchar_t *text) {
    FILE *f = _wfopen(path, L"wb");
    if (f == NULL) {
        return;
    }
    char buf[128];
    if (WideCharToMultiByte(CP_UTF8, 0, text, -1, buf, sizeof(buf), NULL, NULL) > 0) {
        fputs(buf, f);
    }
    fclose(f);
}

/* Запустить и дождаться. (DWORD)-1 — процесс не стартовал. */
static DWORD run_and_wait(const wchar_t *cmd) {
    wchar_t line[512];
    wcscpy_s(line, 512, cmd);
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    if (!CreateProcessW(NULL, line, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
        return (DWORD)-1;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return code;
}

static int dir_exists(const wchar_t *path) {
    DWORD attr = GetFileAttributesW(path);
    return attr != INVALID_FILE_ATTRIBUTES && (attr & FILE_ATTRIBUTE_DIRECTORY);
}

int main(void) {
    wchar_t dir[MAX_PATH];
    if (GetModuleFileNameW(NULL, dir, MAX_PATH) == 0) {
        return 1;
    }
    wchar_t *slash = wcsrchr(dir, L'\\');
    if (slash != NULL) {
        *slash = L'\0';
    }
    SetCurrentDirectoryW(dir);

    /* Состояние пользователя живёт вне папки релиза: обновление заменяет её
     * целиком и не должно уносить с собой ключ, настройки и модели. */
    wchar_t *local = _wgetenv(L"LOCALAPPDATA");
    if (local == NULL) {
        fwprintf(stderr, L"LOCALAPPDATA is not set.\n");
        wait_for_enter();
        return 1;
    }
    wchar_t home[MAX_PATH];
    swprintf_s(home, MAX_PATH, L"%s\\StreamDirector", local);
    _wputenv_s(L"STREAM_DIRECTOR_HOME", home);
    _wputenv_s(L"STREAM_DIRECTOR_INSTALL", dir);
    /* Питон, увидев эту переменную, откроет панель в браузере, когда сервер
     * реально поднимется. sys.path дистрибутива задаёт python312._pth. */
    _wputenv_s(L"STREAM_DIRECTOR_OPEN_PANEL", L"1");

    /* Остался с прошлого самообновления: тогда exe был работающим и его можно
     * было только переименовать. Теперь он никому не нужен. */
    DeleteFileW(L"StreamDirector.exe.old");

    wchar_t version[64];
    if (!read_line_file(L"current.txt", version, 64)) {
        fwprintf(stderr, L"current.txt not found or empty.\n");
        fwprintf(stderr, L"Make sure the zip was fully extracted.\n");
        wait_for_enter();
        return 1;
    }

    wchar_t cmd[512];
    swprintf_s(cmd, 512,
               L"versions\\%s\\python\\python.exe -m stream_director.updater", version);
    if (run_and_wait(cmd) == EXIT_SWITCHED) {
        read_line_file(L"current.txt", version, 64);
    }

    for (;;) {
        swprintf_s(cmd, 512,
                   L"versions\\%s\\python\\python.exe -m stream_director", version);
        DWORD code = run_and_wait(cmd);
        if (code == 0) {
            return 0;
        }
        /* Процесс не стартовал вовсе: папки versions\<V> нет или она побита.
         * Это тоже провал версии, и откат тут нужен даже больше, чем обычно —
         * поэтому не выходим сразу, а идём к нему. */
        int started = (code != (DWORD)-1);

        /* Приложение пишет known-good.txt, только когда сервер поднялся. Файл
         * отличается от current.txt ровно в одном случае: это первый запуск
         * после обновления, и он провалился. */
        wchar_t good[64];
        wchar_t good_dir[MAX_PATH];
        if (read_line_file(L"known-good.txt", good, 64) && wcscmp(good, version) != 0) {
            swprintf_s(good_dir, MAX_PATH, L"versions\\%s", good);
            if (dir_exists(good_dir)) {
                wchar_t msg[512];
                swprintf_s(msg, 512,
                           L"Версия %s, похоже, не запустилась.\n\nВернуться на %s?",
                           version, good);
                if (MessageBoxW(NULL, msg, L"Stream Director",
                                MB_YESNO | MB_ICONWARNING | MB_SETFOREGROUND) == IDYES) {
                    write_line_file(L"current.txt", good);
                    wcscpy_s(version, 64, good);
                    /* Повторный откат невозможен: version стал known-good. */
                    continue;
                }
            }
        }
        if (!started) {
            fwprintf(stderr, L"Failed to start python for version %s.\n", version);
            fwprintf(stderr, L"Make sure the zip was fully extracted.\n");
            wait_for_enter();
            return 1;
        }
        fwprintf(stderr, L"\nStream Director exited with error code %lu.\n", code);
        fwprintf(stderr, L"(Is another copy already running on port 8710?)\n");
        wait_for_enter();
        return (int)code;
    }
}
