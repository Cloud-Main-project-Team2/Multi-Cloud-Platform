variable "region" {
  type        = string
  description = "버킷을 생성할 AWS 리전 (예: ap-northeast-2)"
}

variable "bucket_name" {
  type        = string
  description = "S3 버킷 이름 (계정이 아닌 AWS 전역에서 고유해야 함)"
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "추가 태그"
}

variable "versioning_enabled" {
  type        = bool
  default     = false
  description = "버킷 버전 관리 활성화 여부"
}
