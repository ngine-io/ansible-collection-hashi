job "echo" {
  datacenters = ["dc1"]

  group "web" {
    task "echo" {
      driver = "docker"

      config {
        image = "hashicorp/http-echo"
        args  = ["-text", "hello"]
      }
    }
  }
}
