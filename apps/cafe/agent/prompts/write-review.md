# Đề bài hôm nay — {{today_vn}}

Tìm và viết bài giới thiệu MỘT quán cà phê ở Đà Nẵng theo đúng system prompt.

## Khu vực hợp lệ cho `district` (chọn đúng một)

{{districts}}

## Danh mục `tags` hợp lệ (slug → nghĩa)

{{tags_catalog}}

## Danh mục `amenities` hợp lệ (slug → nghĩa)

{{amenities_catalog}}

## Các quán ĐÃ CÓ BÀI — tuyệt đối KHÔNG viết lại

{{existing_reviews}}

## Gợi ý chọn quán

- Ưu tiên quán có nhiều nguồn nhắc tới (dễ kiểm chứng, nhiều chất liệu để viết).
- Đa dạng hoá theo thời gian: xen kẽ khu vực, kiểu quán (specialty / vintage /
  sân vườn / rooftop / view biển…), đừng dồn một kiểu.
- Quán nhỏ ít người biết nhưng có nguồn tốt là một điểm cộng — blog thích những
  phát hiện như vậy hơn chuỗi lớn ai cũng biết.

Trả về đúng một JSON object như system prompt quy định.
