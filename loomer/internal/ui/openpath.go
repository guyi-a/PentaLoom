// openPath — 调系统默认 app 打开 URL / 本地文件 / 文件夹. 跟 IDE 跳系统浏览器
// 一样的体感. 走 exec (没 shell injection 风险, exec.Command 直接 execve 不
// 经 shell), 加 `--` 分隔器防 path 以 - 开头被命令当 flag 解析.
//
// macOS: open
// linux: xdg-open
// windows: rundll32 url.dll,FileProtocolHandler

package ui

import (
	"fmt"
	"os/exec"
	"runtime"
)

func openPath(path string) error {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		cmd = exec.Command("open", "--", path)
	case "linux":
		cmd = exec.Command("xdg-open", path)
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", path)
	default:
		return fmt.Errorf("openPath: unsupported platform %s", runtime.GOOS)
	}
	// Start 不等命令完成, fire-and-forget. open / xdg-open 自己 fork 走系统 app.
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("openPath %q: %w", path, err)
	}
	return nil
}
