require 'httparty'
require 'nokogiri'
require 'octokit'

# Scrape blog posts from the website
url = "https://www.bengreenberg.dev/blog/"
response = HTTParty.get(url)
parsed_page = Nokogiri::HTML(response.body)
posts = parsed_page.css('.flex.flex-col.rounded-lg.shadow-lg.overflow-hidden')

# Generate the updated blog posts list (top 5)
posts_list = ["\n### Recent Blog Posts\n\n"]
posts.first(5).each do |post|
  title = post.css('p.text-xl.font-semibold.text-gray-900').text.strip
  link = post.at_css('a')[:href]
  posts_list << "* [#{title}](#{link})"
end

# Update the README.md file
client = Octokit::Client.new(access_token: ENV['GITHUB_TOKEN'])
repo = ENV['GITHUB_REPOSITORY']
readme = client.readme(repo)
readme_content = Base64.decode64(readme[:content]).force_encoding('UTF-8')

# Insert the blog posts list after the picture
picture_regex = /(<img src="https:\/\/www.bengreenberg.dev\/assets\/images\/ben_and_matz.jpeg" align="center" style="width: 100%" \/>\n<\/div>)/
updated_content = readme_content.sub(picture_regex, "\\1\n\n#{posts_list.join("\n")}\n")

client.update_contents(repo, 'README.md', 'Update recent blog posts', readme[:sha], updated_content)
