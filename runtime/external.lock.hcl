# Local rebuild of the pinned upstream source; not an official release hash.
provider "registry.terraform.io/hashicorp/external" {
  version     = "2.4.2+patched.1"
  constraints = "2.4.2+patched.1"
  hashes = [
    "h1:9m2UPBjg/L6+pNyGlveXiMM2gMRmh2ygaz6TnQN/LwM=",
  ]
}
