output "instance_name" {
  value = google_compute_instance.vm.name
}

output "self_link" {
  value = google_compute_instance.vm.self_link
}

output "zone" {
  value = google_compute_instance.vm.zone
}

output "machine_type" {
  value = google_compute_instance.vm.machine_type
}

output "network_ip" {
  value = google_compute_instance.vm.network_interface[0].network_ip
}

output "external_ip" {
  value = google_compute_instance.vm.network_interface[0].access_config[0].nat_ip
}
