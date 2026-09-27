# Hugo Blogcard

Hugo Blogcard is a shortcode that builds a link preview card at build time.
It reads metadata and images from the linked page without running its
JavaScript.

## Requirements

- Hugo 0.141.0 or later (Hugo versions before 0.153.0 require the Extended
  edition for WebP encoding)
- Go 1.22.2 or later (the module declares `go 1.22.2`)

## Installation

Initialize Hugo Modules in the root of your site:

```sh
hugo mod init example.com/my-site
```

Add the module to your site's `hugo.toml`:

```toml
[module]
  [[module.imports]]
    path = "github.com/glyzinie/hugo-blogcard"
```

## Usage

Use the shortcode with the linked page as its first positional argument:

```go-html-template
{{< blogcard "https://example.com/article/" >}}
```

When the shortcode is used, `assets/blogcard/blogcard.css` is loaded
automatically as a shared stylesheet. No `<head>` template change is
required. Customize the card with the `.blogcard` class, or override
`assets/blogcard/blogcard.css` in your site.

## Configuration

Set these optional site parameters under `[params]`:

```toml
[params]
  imageQuality = 80
  # Path relative to the site's assets/ directory.
  defaultNoimage = "images/noimage.png"
```

`imageQuality` defaults to `80`. `defaultNoimage` is optional; when provided,
the file at `assets/images/noimage.png` in this example can be used when a
remote image is missing or cannot be processed. Without a usable image, the
card can render without an image.

To read web app manifests served as `application/manifest+json`, add the
following to your site's `hugo.toml`. Hugo otherwise may fail to infer the
resource type and the card will use `/favicon.ico` instead. If you already
set `mediaTypes` under `[security.http]`, append this pattern to that list.

```toml
[security.http]
  mediaTypes = ['(?i)^application/manifest\+json(?:\s*;|$)']
```

This setting trusts that response MIME type; it does not change the URLs or
HTTP methods Hugo is allowed to fetch. The module leaves your site's
security configuration unchanged.

## Build-time behavior

- The linked HTML is fetched during the Hugo build. Static Open Graph,
  Twitter, and regular meta tags, together with the page title, are read from
  the HTML. JavaScript is not executed.
- Missing metadata, fetch errors, and unsupported images fall back to the
  configured replacement image or to a card without an image.
- Favicon discovery prefers a `link` icon. When no icon link is available, the
  first valid icon from a web app manifest is used. The final URL fallback is
  `/favicon.ico`; the target is not guaranteed to provide that file.
- HTTP and image resources use Hugo's existing caches. Refresh behavior follows
  the cache policy configured for Hugo; no fixed download-speed or refresh-time
  guarantee is made.

## Testing

Run the test script with the project's default Hugo binary:

```sh
uv run --no-cache --no-project python tests/test_blogcard.py
```

Set `HUGO_BIN` to test with a different Hugo binary or version:

```sh
HUGO_BIN=/path/to/hugo uv run --no-cache --no-project python tests/test_blogcard.py
```
