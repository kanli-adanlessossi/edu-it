# Build-time package mirror for apk. Default is the Alpine CDN (CI). Override locally with the
# ALPINE_MIRROR environment variable. It never changes the final image's repositories file.
variable "ALPINE_MIRROR" {
  default = "https://dl-cdn.alpinelinux.org/alpine"
}
group "default" {
  targets = ["linux-base", "python"]
}

target "linux-base" {
  context    = "linux-base"
  dockerfile = "Dockerfile"
  args       = { ALPINE_MIRROR = ALPINE_MIRROR }
  tags       = ["edu-it/lab-linux-base:dev"]
}

target "python" {
  context    = "python"
  dockerfile = "Dockerfile"
  # Named context: "FROM linux-base" in python/Dockerfile resolves to the target above.
  contexts = {
    linux-base = "target:linux-base"
  }
  args = { ALPINE_MIRROR = ALPINE_MIRROR }
  tags = ["edu-it/lab-python:dev"]
}
