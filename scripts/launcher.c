/* Stream Director — portable-лаунчер.
 * Запускает python\python.exe -m stream_director из своей директории.
 * Сообщения в консоль — ASCII-английский: кодовая страница консоли на
 * свежей Windows портит кириллицу. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

static void wait_for_enter(void) {
    fwprintf(stderr, L"Press Enter to exit...");
    getwchar();
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

    /* Питон, увидев эту переменную, откроет панель в браузере, когда сервер
     * реально поднимется. sys.path дистрибутива задаёт python312._pth. */
    _wputenv_s(L"STREAM_DIRECTOR_OPEN_PANEL", L"1");

    wchar_t cmd[] = L"python\\python.exe -m stream_director";
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
        fwprintf(stderr, L"Failed to start python\\python.exe (error %lu).\n",
                 GetLastError());
        fwprintf(stderr, L"Make sure the zip was fully extracted.\n");
        wait_for_enter();
        return 1;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    if (code != 0) {
        fwprintf(stderr, L"\nStream Director exited with error code %lu.\n", code);
        fwprintf(stderr, L"(Is another copy already running on port 8710?)\n");
        wait_for_enter();
    }
    return (int)code;
}
