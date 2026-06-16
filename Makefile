.PHONY: loom-build loom-install loom-uninstall loom-dev loom-test loom-clean

# loom + loomer + loomctl build → ./.bin/{loom,loomer,loomctl}
loom-build:
	@mkdir -p .bin
	cd loom && go build -o ../.bin/loom .
	cd loomer && go build -o ../.bin/loomer .
	cd loomctl && go build -o ../.bin/loomctl .
	@ls -lh .bin/

# 装到 ~/.pentaloom/bin/ + launchd plist 自启 daemon
loom-install: loom-build
	@mkdir -p ~/.pentaloom/bin
	cp .bin/loom ~/.pentaloom/bin/loom
	cp .bin/loomer ~/.pentaloom/bin/loomer
	cp .bin/loomctl ~/.pentaloom/bin/loomctl
	~/.pentaloom/bin/loom install

loom-uninstall:
	~/.pentaloom/bin/loom uninstall || true

# 开发: 不装 launchd, 前台跑 daemon (LOOM_LOOMER_PATH 指 .bin) + 开 demo 窗
loom-dev: loom-build
	@echo "→ daemon 后台启动 (用 'pkill -INT -f \"\\.bin/loom start\"' 终止)"
	@LOOM_LOOMER_PATH=$$PWD/.bin/loomer ./.bin/loom start &
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do \
	  if [ -S $$HOME/.pentaloom/loom.sock ]; then break; fi; \
	  sleep 0.2; \
	  if [ "$$i" = "15" ]; then echo "✗ daemon 3s 内没起来, 看 stderr 排查" && exit 1; fi; \
	done
	./.bin/loom open --entry $$PWD/loomer/testdata/hello-app/index.tsx --width 600 --height 420
	@sleep 0.5
	./.bin/loom status
	@echo "→ 关 demo 窗后跑 'make loom-dev-stop' 停 daemon"

loom-dev-stop:
	pkill -INT -f "\.bin/loom start" || true

loom-test:
	cd loomer && go test ./...
	cd loom && go test ./...
	cd loomctl && go test ./...

loom-clean:
	rm -rf .bin
