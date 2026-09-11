variable "distribution_name" {
  type        = string
  description = "배포 설명(comment)으로 쓸 이름 — CloudFront 배포엔 Name 태그 개념이 없어 콘솔 식별용으로만 쓰인다."
}

variable "origin_domain_name" {
  type        = string
  description = "오리진 도메인 이름(예: S3 버킷의 리전 도메인, 그 외 HTTPS 오리진). HTTPS(443)로만 접속한다."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "추가 태그"
}
