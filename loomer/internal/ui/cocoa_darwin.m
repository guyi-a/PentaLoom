// Cocoa bridge 实现 — Obj-C 端调 NSWindow / WKWebView selector. cgo 识别 .m
// 文件按 Objective-C 编译, _darwin.m 命名让 non-darwin build 自动跳过.
//
// dispatch_async(main) 是兜底: webview Bind callback 实测在 main thread 上跑,
// 直接调 selector 也 OK; 但 future 改动可能让 callback 跑别的 thread, 加
// dispatch_async 防御性确保 selector 命中主线程 (NSWindow / WKWebView 操作必须主线程).

#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

void loomer_miniaturize(uintptr_t nsWindowPtr) {
    NSWindow *w = (__bridge NSWindow *)(void *)nsWindowPtr;
    dispatch_async(dispatch_get_main_queue(), ^{
        [w miniaturize:nil];
    });
}

void loomer_zoom(uintptr_t nsWindowPtr) {
    NSWindow *w = (__bridge NSWindow *)(void *)nsWindowPtr;
    dispatch_async(dispatch_get_main_queue(), ^{
        [w zoom:nil];
    });
}

// 把 errMsg 拷进 errBuf (长度 < cap 含 \0). errBuf 为空指针不写.
static void loomer_set_err(char *errBuf, size_t cap, const char *errMsg) {
    if (errBuf == NULL || cap == 0) return;
    size_t n = strlen(errMsg);
    if (n >= cap) n = cap - 1;
    memcpy(errBuf, errMsg, n);
    errBuf[n] = '\0';
}

// 从 NSWindow 的 contentView 找 WKWebView. webview_go 里 webview 即 NSWindow
// 的 contentView, 但留 subviews 兜底防 future 改动套了一层 wrapper.
static WKWebView *loomer_find_webview(NSWindow *w) {
    if (w == nil) return nil;
    NSView *root = [w contentView];
    if ([root isKindOfClass:[WKWebView class]]) {
        return (WKWebView *)root;
    }
    for (NSView *sub in [root subviews]) {
        if ([sub isKindOfClass:[WKWebView class]]) {
            return (WKWebView *)sub;
        }
        // 再往下一层 (够用, webview_go 实测 contentView 直接是 webview)
        for (NSView *grand in [sub subviews]) {
            if ([grand isKindOfClass:[WKWebView class]]) {
                return (WKWebView *)grand;
            }
        }
    }
    return nil;
}

// 截图实现 — WKWebView takeSnapshotWithConfiguration: (macOS 11+, async)
// 流程:
//   1. 主线程从 NSWindow 找 WKWebView
//   2. 主线程 takeSnapshot, completionHandler 拿 NSImage
//   3. NSImage → TIFFRepresentation → NSBitmapImageRep → PNG NSData
//   4. malloc 拷贝 bytes 返
//   dispatch_semaphore_t 让调用方阻塞到 PNG 出来 (5s 超时)
unsigned char *loomer_screenshot(uintptr_t nsWindowPtr, size_t *out_len,
                                 char *errBuf, size_t errBufCap) {
    if (out_len == NULL) return NULL;
    *out_len = 0;
    if (nsWindowPtr == 0) {
        loomer_set_err(errBuf, errBufCap, "nsWindow pointer is 0");
        return NULL;
    }
    NSWindow *w = (__bridge NSWindow *)(void *)nsWindowPtr;

    __block WKWebView *webview = nil;
    dispatch_sync(dispatch_get_main_queue(), ^{
        webview = loomer_find_webview(w);
    });
    if (webview == nil) {
        loomer_set_err(errBuf, errBufCap, "WKWebView not found in NSWindow contentView");
        return NULL;
    }

    __block NSData *pngData = nil;
    __block NSString *errMsg = nil;
    dispatch_semaphore_t sem = dispatch_semaphore_create(0);

    dispatch_async(dispatch_get_main_queue(), ^{
        WKSnapshotConfiguration *cfg = [[WKSnapshotConfiguration alloc] init];
        // 默认 takeSnapshot 截当前可见 view (rect 用 webview 自身 bounds)
        [webview takeSnapshotWithConfiguration:cfg
                            completionHandler:^(NSImage *img, NSError *err) {
            if (err != nil) {
                errMsg = [err localizedDescription];
            } else if (img != nil) {
                NSData *tiff = [img TIFFRepresentation];
                if (tiff != nil) {
                    NSBitmapImageRep *rep = [NSBitmapImageRep imageRepWithData:tiff];
                    if (rep != nil) {
                        pngData = [rep representationUsingType:NSBitmapImageFileTypePNG
                                                    properties:@{}];
                    }
                }
                if (pngData == nil) {
                    errMsg = @"NSImage → PNG encode failed";
                }
            } else {
                errMsg = @"takeSnapshot returned nil image without error";
            }
            dispatch_semaphore_signal(sem);
        }];
    });

    // 5s 超时 — webview 内容静止时 takeSnapshot 通常 < 100ms; 5s 够缓冲.
    long timed_out = dispatch_semaphore_wait(sem,
        dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC));
    if (timed_out != 0) {
        loomer_set_err(errBuf, errBufCap, "takeSnapshot timeout (5s)");
        return NULL;
    }

    if (errMsg != nil) {
        loomer_set_err(errBuf, errBufCap, [errMsg UTF8String]);
        return NULL;
    }
    if (pngData == nil || [pngData length] == 0) {
        loomer_set_err(errBuf, errBufCap, "snapshot returned no data");
        return NULL;
    }

    NSUInteger len = [pngData length];
    unsigned char *buf = (unsigned char *)malloc(len);
    if (buf == NULL) {
        loomer_set_err(errBuf, errBufCap, "malloc failed");
        return NULL;
    }
    memcpy(buf, [pngData bytes], len);
    *out_len = len;
    return buf;
}
